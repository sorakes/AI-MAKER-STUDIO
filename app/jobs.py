"""Fila de render por API.

Um job = um nó do canvas. Os passos são escolhidos pelo que está ligado no nó e pelo que
os modelos sabem fazer:
  imagem                -> [imagem]
  vídeo com 1º frame    -> [vídeo imagem→vídeo]
  vídeo só com texto    -> [vídeo texto→vídeo]  ou  [imagem, vídeo imagem→vídeo] (still primeiro)
  transição A→B         -> [vídeo com frame inicial + final]
O pedido ao provedor fica salvo (polling_url): se o app reiniciar no meio, o job retoma de onde parou.
"""
import asyncio
import json
import time
import urllib.parse
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import audio as sound, db, videoedit, lipsync, media, montage, outpaint, promptgen, providers, registry, relight, storage, subtitles, upscale, usage
from .auth import CURRENT, public_user

JOBS: dict[str, dict] = {}


class RenderRequest(BaseModel):
    node_id: str = ""
    name: str = "Plano"
    gen: str = "video"                 # image | video | bridge
    model: str | int | None = "auto"   # id do modelo do nó (vídeo ou imagem)
    image_model: str | int | None = "auto"  # still do 1º frame quando o vídeo começa por imagem gerada
    start_mode: str = "auto"           # auto | still | text
    prompt: str
    keyframe_prompt: str | None = None
    generate_audio: bool = False       # performance: o modelo de vídeo gera a fala (voz e boca)
    negative: str = ""
    aspect: str = "16:9"
    duration: float = 5
    quality: str = "draft"             # draft | final
    seed: int | None = None
    start_image_url: str | None = None
    end_image_url: str | None = None
    ref_image_urls: list[str] = Field(default_factory=list)
    adapt: bool = True                 # reescreve o prompt de vídeo para bater com o still gerado
    require_refs: bool = False         # posições de personagem/produto: só modelos que recebem as referências
    compose: bool = False              # 1º frame conectado + elenco: gera antes um 1º frame com o elenco na cena
    direction_image_url: str | None = None  # desenho da câmera/elementos sobre o 1º frame (referência do vídeo)
    upscale_factor: int = 0  # gen=upscale: fator final (2 ou 4)
    target_size: list[int] = Field(default_factory=list)  # tamanho exato da saída (edições com IA); a IA não o garante
    local_op: dict = Field(default_factory=dict)  # gen=relight/style/outpaint/inpaint: operação do ajuste local (Pillow)
    composite: dict = Field(default_factory=dict)  # outpaint/inpaint: recompõe o original sobre o que a máscara protege


# modelos virtuais do job (sem entrada no catálogo): transcrição e áudio
VIRTUAL = {subtitles.STT_ID: subtitles.STT_LOCAL, **sound.VIRTUAL}

def _step(kind: str, m: dict, label: str) -> dict:
    return {"kind": kind, "model": m["id"], "model_label": m["label"], "provider": m["provider"],
            "label": label, "status": "pending", "output": None, "handle": None, "cost": 0.0}


def plan(user: dict, r: RenderRequest) -> list[dict]:
    refs_need = {"refs": True} if r.ref_image_urls else {"t2i": True}
    if r.gen == "upscale":  # IA com referências se houver modelo; senão upscale simples local
        m = upscale.pick(user, r.model)
        return [_step("image", m or upscale.LOCAL, "Upscale criativo" if m else "Upscale simples")]
    if r.gen in sound.GEN_MODEL:  # voz, música ou efeito: custo real do provedor (efeito é local)
        return [_step("audio", sound.GEN_MODEL[r.gen], sound.LABELS[r.gen])]
    if r.gen == "transcribe":  # áudio -> texto: modelo de áudio do OpenRouter (o mais barato com entrada de áudio)
        return [_step("text", subtitles.STT_LOCAL, "Transcrição")]
    if r.gen == "subtitles":  # legendas queimadas com ffmpeg (local)
        return [_step("video", relight.local_model("Legendas"), "Legendas")]
    if r.gen == "lipsync":  # lip sync real se houver modelo habilitado; senão animação simples com o áudio
        m = lipsync.pick(user, r.model)
        label = "Fala com IA" if m else "Animação simples"
        return [_step("video", m or relight.local_model(label), label)]
    if r.gen in ("outpaint", "inpaint"):  # IA com referências se houver modelo; senão preenchimento simples
        m = outpaint.pick(user, r.model)
        label = ("Expandir" if r.gen == "outpaint" else "Editar região") + (" com IA" if m else " simples")
        return [_step("image", m or relight.local_model(label), label)]
    if r.gen in ("relight", "style"):  # IA com referências se houver modelo; senão ajuste local (Pillow)
        m = relight.pick(user, r.model)
        label = ("Relight" if r.gen == "relight" else "Estilo") + (" com IA" if m else " simples")
        return [_step("image", m or relight.local_model(label), label)]

    def image_model(choice):
        m = None
        count = len(r.ref_image_urls) + (1 if r.compose and r.start_image_url else 0)
        if count and choice in (None, "", "auto"):  # muitas referências: o que aceite todas
            pool = [x for x in registry.for_user(user, "image") if x["caps"].get("refs")]
            fit = sorted([x for x in pool if (x["caps"].get("max_refs") or 4) >= count],
                         key=lambda x: (not x.get("is_default"), x.get("cost") or 0))
            m = fit[0] if fit else (max(pool, key=lambda x: x["caps"].get("max_refs") or 4) if pool else None)
        m = m or registry.pick(user, "image", choice, refs_need)
        if not m and r.require_refs:
            raise HTTPException(400, "Este passo precisa de um modelo de imagem que aceite referências "
                                "(o escolhido não aceita ou nenhum está liberado). Sem as referências o personagem se perde.")
        if not m and r.ref_image_urls:  # nenhum modelo com referências: segue só com texto
            m = registry.pick(user, "image", choice, {"t2i": True})
        if not m:
            raise HTTPException(400, "Nenhum modelo de imagem disponível para este pedido (veja o painel admin)"
                                if choice in (None, "", "auto") else "O modelo de imagem escolhido não atende "
                                "este pedido (precisa de referências ou não aceita texto puro)")
        return m

    wants_refs = bool(r.ref_image_urls or r.direction_image_url)

    def video_model(need: dict, what: str):
        # Automático: com referências/desenho, prefere um modelo que receba várias imagens + texto
        auto = r.model in (None, "", "auto")
        m = None
        if wants_refs and auto:  # prefere quem respeitou cena + produto + texto no teste; depois qualquer um com referências
            good = [x for x in registry.for_user(user, "video") if x["caps"].get("refs") and not x["caps"].get("refs_weak")
                    and all(x["caps"].get(k) for k, v in need.items() if v)]
            m = sorted(good, key=lambda x: (not x.get("is_default"), x.get("cost") or 0))[0] if good else None
            m = m or registry.pick(user, "video", r.model, {**need, "refs": True})
        m = m or registry.pick(user, "video", r.model, need)
        if not m:
            raise HTTPException(400, f"Nenhum modelo de vídeo {what} disponível" if r.model in (None, "", "auto")
                                else f"O modelo de vídeo escolhido não faz {what}")
        return m

    if r.gen == "videoedit":  # edição por quadros: um modelo de imagem por quadro (custo = quadros × imagem)
        return [_step("video", image_model(r.image_model), "Edição por quadros")]
    if r.gen == "image":
        return [_step("image", image_model(r.model), "Imagem")]
    if r.gen == "bridge":
        if not (r.start_image_url and r.end_image_url):
            raise HTTPException(400, "Transição precisa do frame de A e do frame de B")
        return [_step("video", video_model({"i2v": True, "end_frame": True}, "com frame inicial + final"), "Transição A→B")]
    if r.start_image_url and r.compose and r.ref_image_urls:
        m = image_model(r.image_model)
        if not m or not m["caps"].get("refs"):
            raise HTTPException(400, "Para colocar o elenco no 1º frame é preciso um modelo de imagem que aceite referências")
        return [_step("image", m, "1º frame com o elenco"), _step("video", video_model({"i2v": True}, "imagem→vídeo"), "Vídeo a partir do 1º frame")]
    if r.start_image_url:
        return [_step("video", video_model({"i2v": True}, "imagem→vídeo"), "Vídeo a partir do 1º frame")]
    direct = None
    if r.start_mode != "still":
        direct = registry.pick(user, "video", r.model, {"t2v": True, "refs": True}) if r.ref_image_urls and r.model in (None, "", "auto") else None
        direct = direct or registry.pick(user, "video", r.model, {"t2v": True})
        if direct and r.ref_image_urls and not direct["caps"].get("refs") and r.start_mode != "text":
            direct = None  # o modelo ignoraria as referências: gera antes o 1º frame com elas
    if direct:
        return [_step("video", direct, "Vídeo a partir do texto")]
    if r.start_mode == "text":
        raise HTTPException(400, "Nenhum modelo texto→vídeo disponível")
    return [_step("image", image_model(r.image_model), "1º frame (imagem)"),
            _step("video", video_model({"i2v": True}, "imagem→vídeo"), "Vídeo a partir do 1º frame")]


_SAVE_SQL = ("INSERT INTO jobs(id, user_id, created, status, data) VALUES(?,?,?,?,?) "
             "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data")


def _save_args(job: dict) -> tuple:
    return (job["id"], job["user_id"], job["created"], job["status"], json.dumps(job, ensure_ascii=False))


def _save(job: dict):
    db.run(_SAVE_SQL, _save_args(job))


async def asave(job: dict):
    """Versão async (aiosqlite) de _save: usada pelas tarefas de render, que rodam no event loop."""
    await db.arun(_SAVE_SQL, _save_args(job))


def _visible(job: dict | None, user: dict) -> dict:
    if not job or (job["user_id"] != user["id"] and not user.get("is_admin", user.get("role") == "admin")):
        raise HTTPException(404, "Job não encontrado")
    return job


def get(job_id: str, user: dict) -> dict:
    job = JOBS.get(job_id)
    if not job:
        row = db.one("SELECT data FROM jobs WHERE id = ?", (job_id,))
        job = json.loads(row["data"]) if row else None
    return _visible(job, user)


async def aget(job_id: str, user: dict) -> dict:
    job = JOBS.get(job_id)
    if not job:
        row = await db.aone("SELECT data FROM jobs WHERE id = ?", (job_id,))
        job = json.loads(row["data"]) if row else None
    return _visible(job, user)


def list_for(user: dict, limit: int = 50) -> list[dict]:
    rows = db.all_("SELECT data FROM jobs WHERE user_id = ? ORDER BY created DESC LIMIT ?", (user["id"], limit))
    return [JOBS.get(json.loads(r["data"])["id"]) or json.loads(r["data"]) for r in rows]


async def alist_for(user: dict, limit: int = 50) -> list[dict]:
    rows = await db.aall("SELECT data FROM jobs WHERE user_id = ? ORDER BY created DESC LIMIT ?", (user["id"], limit))
    return [JOBS.get(json.loads(r["data"])["id"]) or json.loads(r["data"]) for r in rows]


def _pending(uid: int) -> dict:
    """Pendências do usuário lidas do banco (fonte da verdade); o dict JOBS é só cache de quem está rodando."""
    out = {"images": 0, "videos": 0, "spend": 0.0}
    for row in db.all_("SELECT data FROM jobs WHERE user_id = ? AND status IN ('queued', 'running')", (uid,)):
        j = json.loads(row["data"])
        todo = [s for s in j["steps"] if s["status"] != "done"]
        out["images"] += sum(s["kind"] == "image" for s in todo)
        out["videos"] += sum(s["kind"] == "video" for s in todo)
        out["spend"] += sum(s.get("est", 0) for s in todo)
    return out


def prepare(user: dict, r: RenderRequest) -> dict:
    """Checagens, planejamento e gravação do job. Síncrono: roda em thread (não toca o event loop)."""
    # Toda mídia de entrada precisa ser do próprio usuário.
    for u in [r.start_image_url, r.end_image_url, r.direction_image_url, *r.ref_image_urls]:
        if u:
            storage.resolve(u, user)
    storage.check_quota(user)
    steps = plan(user, r)
    perms = user.get("permissions") or {}
    if perms:
        from_asset = r.node_id.startswith("asset:")  # posições de personagem/produto (checadas na rota)
        video = any(s["kind"] == "video" for s in steps)
        need = ["gen_video"] if video else ["gen_image"]
        if not from_asset:
            need.append("node_video" if video else "node_image")
        labels = {"gen_video": "gerar vídeos", "gen_image": "gerar imagens", "node_video": "usar o node Gerar vídeo",
                  "node_image": "usar o node Gerar imagem"}
        for p in need:
            if not perms.get(p, True):
                raise HTTPException(403, f"Sem permissão para {labels[p]}. Fale com o administrador.")
    models = {s["model"]: (upscale.LOCAL if s["provider"] == "local" else VIRTUAL[s["model"]] if s["model"] in VIRTUAL
                           else registry.get(s["model"])) for s in steps}
    for s in steps:
        s["est"] = registry.estimate(models[s["model"]], r.duration if s["kind"] == "video" else 1) * (videoedit.frame_count(r.duration) if r.gen == "videoedit" else 1)  # um modelo de imagem por quadro
    est = sum(s["est"] for s in steps)
    usage.check(user, images=sum(s["kind"] == "image" for s in steps), videos=sum(s["kind"] == "video" for s in steps),
                est_cost=est, model_ids=[k for k in models if k != upscale.LOCAL_ID], pending=_pending(user["id"]))
    job = {"id": f"job-{uuid.uuid4().hex[:10]}", "user_id": user["id"], "created": time.time(), "status": "queued",
           "progress": 0, "detail": None, "error": None, "spec": r.model_dump(), "steps": steps,
           "est_cost": round(est, 4), "output": None, "media": None, "keyframe": None}
    JOBS[job["id"]] = job
    _save(job)
    return job


_LOOP: asyncio.AbstractEventLoop | None = None  # loop do servidor (ligado no startup), para chamadas vindas de threads


def bind_loop(loop: asyncio.AbstractEventLoop):
    global _LOOP
    _LOOP = loop


def start(job: dict, user: dict, resume: bool = False):
    """Agenda a tarefa do render no event loop do servidor. Funciona no loop e também a partir de threads."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _LOOP
        if loop is None:
            raise RuntimeError("event loop do servidor não está ligado (bind_loop)")
        loop.call_soon_threadsafe(loop.create_task, _run(job, user, resume=resume))
        return
    loop.create_task(_run(job, user, resume=resume))


def submit(user: dict, r: RenderRequest) -> dict:
    """prepare + start: usado por quem já está em thread (assets, sprites)."""
    job = prepare(user, r)
    start(job, user)
    return job


def _resume_plan() -> list[tuple[dict, dict]]:
    """Síncrono (thread): marca como interrompidos os jobs que não dá mais para retomar; devolve os vivos."""
    out = []
    for row in db.all_("SELECT data FROM jobs WHERE status IN ('queued', 'running')"):
        job = json.loads(row["data"])
        u = db.one("SELECT * FROM users WHERE id = ?", (job["user_id"],))
        live = u and all(s["status"] in ("done", "pending") or (s["status"] == "running" and s.get("handle"))
                         for s in job["steps"])
        if not live:
            job["status"], job["error"] = "interrupted", "O servidor reiniciou durante o render"
            _save(job)
            continue
        JOBS[job["id"]] = job
        out.append((job, public_user(u)))
    return out


async def resume_all():
    """Na subida do app: retoma jobs que estavam esperando o provedor (leitura no banco fora do loop)."""
    for job, user in await asyncio.to_thread(_resume_plan):
        start(job, user, resume=True)


def _ext(url: str, kind: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    for e in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov"):
        if path.endswith(e):
            return e
    if url.startswith("data:image/"):
        return "." + url[11:url.index(";")].replace("jpeg", "jpg")
    return ".mp4" if kind == "video" else ".png"


def _store_bytes(job: dict, idx: int, data: bytes, ext: str) -> str:
    dest = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_{idx}{ext}"
    dest.write_bytes(data)
    return storage.to_url(dest)


VIDEO_TIMEOUT_S = 45 * 60  # teto do polling de vídeo no provedor


async def _or_video(job: dict, st: dict, m: dict, i: int, n: int, prompt: str, spec: RenderRequest,
                    prev_image: str | None, ref_urls: list[str] = ()) -> bytes:
    """Vídeo pelo OpenRouter: envia o job, acompanha o polling_url e baixa o MP4."""
    if not st.get("handle"):
        as_data = lambda u: media.data_url(storage.resolve(u)) if u else None  # noqa: E731
        body = registry.build_or_video_input(
            m, prompt=prompt, start=await asyncio.to_thread(as_data, prev_image),
            end=await asyncio.to_thread(as_data, spec.end_image_url),
            refs=await asyncio.to_thread(lambda: [as_data(u) for u in ref_urls[:max(1, m["caps"].get("max_refs") or 4)]]),
            aspect=spec.aspect, duration=spec.duration, quality=spec.quality, seed=spec.seed,
            generate_audio=spec.generate_audio)
        st["handle"] = await asyncio.to_thread(providers.or_video_submit, body, m.get("key_id"))
        st["started"] = time.time()
        await asave(job)
    errors = 0
    deadline = st.get("started", time.time()) + VIDEO_TIMEOUT_S
    last_saved = 0.0
    while True:
        if time.time() > deadline:
            raise providers.ProviderError(f"timeout: o vídeo não ficou pronto em {VIDEO_TIMEOUT_S // 60} min")
        await asyncio.sleep(5)
        if time.time() - last_saved > 15:  # progresso no banco: após reinício a tela mostra o último estado
            last_saved = time.time()
            await asave(job)
        try:
            s = await asyncio.to_thread(providers.or_video_status, st["handle"])
            errors = 0
        except providers.ProviderError:
            errors += 1
            if errors > 10:
                raise
            continue
        state = (s.get("status") or "").lower()
        elapsed = time.time() - st.get("started", time.time())
        if state == "completed":
            return await asyncio.to_thread(providers.or_video_download, st["handle"], s)
        if state in ("failed", "cancelled", "expired"):
            raise providers.ProviderError(f"vídeo {state}: {str(s.get('error') or '')[:300]}")
        mins = lambda t: f"{t / 60:.0f} min" if t >= 90 else f"{t:.0f}s"  # noqa: E731
        if state in ("pending", "queued"):
            # ainda não começou no provedor: não finge progresso
            job["detail"] = f"na fila do provedor há {mins(elapsed)}" + (" (fila cheia, pode demorar)" if elapsed > 300 else "")
            job["progress"] = int((i + 0.02) / n * 100)
            st.pop("running_since", None)
        else:
            st.setdefault("running_since", time.time())
            run = time.time() - st["running_since"]
            job["detail"] = f"gerando · {mins(run)}" + (f" (esperou {mins(elapsed - run)} na fila)" if elapsed - run > 20 else "")
            job["progress"] = int((i + 0.05 + min(0.9, run / 150)) / n * 100)


def _store(job: dict, idx: int, url: str, kind: str) -> str:
    data = providers.download(url)
    dest = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_{idx}{_ext(url, kind)}"
    dest.write_bytes(data)
    return storage.to_url(dest)


async def _run(job: dict, user: dict, resume: bool = False):
    CURRENT.set(user)
    spec = RenderRequest(**job["spec"])
    job["status"] = "running"
    prev_image = spec.start_image_url
    n = len(job["steps"])
    try:
        for i, st in enumerate(job["steps"]):
            if st["status"] == "done":
                if st["kind"] == "image":
                    prev_image = st["output"]
                continue
            m = (upscale.LOCAL if st["model"] == upscale.LOCAL_ID else VIRTUAL[st["model"]] if st["model"] in VIRTUAL
                 else await registry.aget(st["model"]))
            if not m:
                raise RuntimeError("modelo removido do painel durante o render")
            st["status"] = "running"
            job["progress"] = int(i / n * 100)
            prompt = spec.keyframe_prompt or spec.prompt if st["kind"] == "image" else spec.prompt
            step_refs = list(spec.ref_image_urls)
            if st["kind"] == "video" and not m["caps"].get("refs"):
                step_refs = []  # modelo não recebe referências: nem elenco nem desenho de câmera (a câmera vai pelo texto)
            elif st["kind"] == "video":
                # referência→vídeo no OpenRouter: o 1º frame vai como imagem 1 da lista
                lead = 1 if prev_image and m["fields"].get("api") == "openrouter_video" else 0
                diag = 1 if spec.direction_image_url else 0
                if diag:
                    step_refs = [spec.direction_image_url] + step_refs
                step_refs = step_refs[:max(1, (m["caps"].get("max_refs") or 4) - lead)]  # acima do limite: corta o fim
                diag = min(diag, len(step_refs))
                n_cast = len(step_refs) - diag
                parts = []
                if lead:
                    parts.append("Reference image 1 is the OPENING FRAME: the video starts exactly from it.")
                if diag:
                    parts.append(f"Reference image {lead + 1} is the DIRECTOR'S MOTION DIAGRAM drawn over the first frame: the purple "
                                 "line is the camera route (dot = start, arrow = end) and each other color marks an element (circled) "
                                 "and where it moves (a line from a solid dot to a hollow ring), as explained below. Use it ONLY as instructions: NEVER draw lines, "
                                 "arrows, circles, dots or labels in the video.")
                if n_cast:
                    a = lead + diag + 1
                    parts.append((f"Reference image {a} shows" if n_cast == 1 else f"Reference images {a}-{a + n_cast - 1} show")
                                 + " the characters/objects to keep identical (same look, shape, colors, labels).")
                if parts:
                    prompt = " ".join(parts) + " " + prompt
            if st["kind"] == "image":  # limite de referências do modelo (com compose, a cena ocupa uma vaga)
                step_refs = step_refs[:max(1, (m["caps"].get("max_refs") or 4) - (1 if spec.compose and spec.start_image_url else 0))]
            if st["kind"] == "image" and spec.compose and spec.start_image_url:
                # elenco = imagens 1..N; a ÚLTIMA é a cena conectada (fundo/enquadramento a manter)
                step_refs = step_refs + [spec.start_image_url]
                prompt = (f"The LAST reference image (image {len(step_refs)}) is the scene: keep its environment, framing, "
                          f"camera angle and lighting exactly. {'Image 1 shows' if len(step_refs) == 2 else f'Images 1-{len(step_refs) - 1} show'} the cast/objects: place them into "
                          f"that scene looking exactly like the references (same face, hair, outfit, shape, colors, labels); ignore the "
                          f"backgrounds of those references. {prompt}")
            if st["kind"] == "video" and i > 0 and spec.adapt and not st.get("handle"):
                job["detail"] = "ajustando o prompt ao 1º frame"
                prompt, caption = await asyncio.to_thread(promptgen.adapt_to_frame, spec.prompt, prev_image)
                job["spec"]["prompt"], job["first_frame_caption"] = prompt, caption
            try:
                if spec.gen == "videoedit":  # edição por quadros: cada quadro vai ao modelo de imagem; o resto volta original
                    job["detail"] = "editando os quadros"
                    dest = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_{i}.mp4"
                    op = {**spec.local_op, "user_id": job["user_id"]}
                    src = storage.resolve(spec.ref_image_urls[0], user)
                    cost = await asyncio.to_thread(videoedit.run, src, op, dest,
                                                   lambda prompt, frame_url: _openrouter_image(m, prompt, spec, [frame_url]))
                    st["output"], url = storage.to_url(dest), None
                elif spec.gen in sound.GEN_MODEL:  # voz, música ou efeito: arquivo de áudio (mp3) guardado no render
                    job["detail"] = f"gerando {sound.LABELS[spec.gen].lower()}"
                    dest = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_{i}.mp3"
                    cost = await asyncio.to_thread(sound.run, spec.gen, spec, dest, m)
                    st["output"], url = storage.to_url(dest), None
                elif spec.gen == "transcribe":  # áudio do vídeo -> WAV -> modelo de áudio -> segmentos (guardados no job)
                    job["detail"] = "transcrevendo o áudio"
                    video = storage.resolve(spec.ref_image_urls[0], user)
                    wav = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_audio.wav"
                    await asyncio.to_thread(subtitles.wav_from_video, video, wav)
                    job["result"] = await asyncio.to_thread(subtitles.transcribe_wav, wav, spec.local_op.get("language", ""))
                    cost = registry.estimate(m, 1)
                    st["output"], url = None, None
                elif spec.gen == "lipsync" and m["provider"] == "openrouter":  # lip sync real (gancho do provedor)
                    job["detail"] = "gerando a fala"
                    data = await asyncio.to_thread(lipsync.remote, m, spec.ref_image_urls[0],
                                                   spec.local_op.get("audio_url"), spec.keyframe_prompt or spec.prompt)
                    cost = registry.estimate(m, spec.duration)
                    st["output"] = await asyncio.to_thread(_store_bytes, job, i, data, ".mp4")
                    url = None
                elif m["provider"] == "openrouter" and st["kind"] == "video":
                    data = await _or_video(job, st, m, i, n, prompt, spec, prev_image, step_refs)
                    cost = registry.estimate(m, spec.duration)
                    job["detail"] = "salvando vídeo"
                    st["output"] = await asyncio.to_thread(_store_bytes, job, i, data, ".mp4")
                    url = None
                elif m["provider"] == "local":  # upscale/relight/estilo simples: Pillow, sem provedor
                    src = storage.resolve(spec.ref_image_urls[0], user)
                    dest = storage.user_dir(job["user_id"], "renders") / f"{job['id']}_{i}{'.mp4' if spec.gen in ('lipsync', 'loop', 'subtitles') else '.png'}"
                    if spec.gen == "upscale":
                        await asyncio.to_thread(upscale.resize_file, src, spec.upscale_factor, dest)
                    elif spec.gen == "subtitles":  # queima as legendas e grava o .srt ao lado do vídeo
                        srt = await asyncio.to_thread(subtitles.burn, src, spec.local_op, dest)
                        job["result"] = {"srt_url": storage.to_url(srt)}
                    elif spec.gen == "lipsync":
                        audio = storage.resolve(spec.local_op["audio_url"], user)
                        await asyncio.to_thread(lipsync.apply_local, spec.local_op, src, audio, dest)
                    elif spec.gen in ("outpaint", "inpaint"):
                        paths = {k[:-4]: str(storage.resolve(v, user)) for k, v in spec.local_op.items() if k.endswith("_url")}
                        await asyncio.to_thread(outpaint.apply_local, spec.local_op, paths, dest)
                    else:
                        ref = storage.resolve(spec.ref_image_urls[1], user) if spec.gen == "style" else None
                        await asyncio.to_thread(relight.apply_local, spec.local_op, src, dest, ref)
                    # já gravado: nada a baixar; legendas traduzidas cobram a tradução (custo real do provedor)
                    st["output"], cost, url = storage.to_url(dest), float((spec.local_op or {}).get("translate_usd") or 0.0), None
                elif m["provider"] == "openrouter":
                    url, cost = await asyncio.to_thread(_openrouter_image, m, prompt, spec, step_refs)
                else:
                    raise RuntimeError(f"provedor {m['provider']} não é mais suportado")
                if url:
                    job["detail"] = "baixando resultado"
                    st["output"] = await asyncio.to_thread(_store, job, i, url, st["kind"])
                    if spec.target_size:  # IA devolve tamanho próprio: leva exatamente ao tamanho do original (× fator)
                        job["detail"] = "ajustando ao tamanho final"
                        await asyncio.to_thread(upscale.resize_to, storage.resolve(st["output"]), tuple(spec.target_size))
                    elif spec.upscale_factor:
                        job["detail"] = "ajustando ao tamanho final"
                        await asyncio.to_thread(upscale.resize_file, storage.resolve(st["output"]), spec.upscale_factor)
                    if spec.composite:  # a área protegida volta a ser o original, byte a byte na composição
                        job["detail"] = "recompondo a área protegida"
                        c = spec.composite
                        composed = await asyncio.to_thread(outpaint.finish_ai, storage.resolve(st["output"]),
                                                           storage.resolve(c["original_url"], user), storage.resolve(c["keep_mask_url"], user),
                                                           tuple(c["size"]), storage.resolve(st["output"]))
                        st["output"] = storage.to_url(composed)
            except Exception as e:
                await usage.alog(st["kind"], provider=m["provider"], model=m["model_id"], status="error",
                          detail={"job": job["id"], "error": str(e)[:400]})
                raise
            st["cost"] = cost
            st["status"] = "done"
            await usage.alog(st["kind"], provider=m["provider"], model=m["model_id"], cost=cost,
                      detail={"job": job["id"], "node": spec.name, "key": m.get("key_name") or ""})
            if st["kind"] == "image":
                prev_image = st["output"]
            await asave(job)
        job["output"] = job["steps"][-1]["output"]
        job["keyframe"] = prev_image
        job["media"] = await asyncio.to_thread(media.describe, storage.resolve(job["output"], user)) if job["output"] else None
        job["status"], job["progress"], job["detail"] = "done", 100, None
    except Exception as e:  # noqa: BLE001 - a mensagem vai para o nó
        job["status"], job["error"], job["detail"] = "error", str(e)[:600], None
        for st in job["steps"]:
            if st["status"] == "running":
                st["status"] = "error"
    await asave(job)
    JOBS.pop(job["id"], None)
    try:
        from . import assets
        await asyncio.to_thread(assets.on_job_done, job)
    except Exception as e:  # noqa: BLE001 - nunca derruba o job
        print(f"[assets] {e}")


def _openrouter_image(m: dict, prompt: str, spec: RenderRequest, ref_urls: list[str] | None = None) -> tuple[str, float]:
    urls = list(spec.ref_image_urls if ref_urls is None else ref_urls)
    mx = m["caps"].get("max_refs") or 4
    if len(urls) > mx:  # acima do limite do modelo: mantém as primeiras (e a cena, que no compose vem por último)
        urls = urls[:mx - 1] + [urls[-1]] if spec.compose and spec.start_image_url else urls[:mx]
    refs = [media.data_url(storage.resolve(u)) for u in urls]
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": u}} for u in refs]
    msg, cost = providers.openrouter_chat(
        m["model_id"], [{"role": "user", "content": content if refs else prompt}], key_id=m.get("key_id"),
        extra={"modalities": ["image", "text"], "image_config": {"aspect_ratio": spec.aspect}, **m["params"]},
        timeout=300)
    images = msg.get("images") or []
    if not images:
        raise providers.ProviderError(f"o modelo não devolveu imagem: {str(msg.get('content'))[:200]}")
    return images[0]["image_url"]["url"], cost or registry.estimate(m)


