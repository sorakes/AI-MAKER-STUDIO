"""API do estúdio: tudo é do usuário logado (arquivos, jobs, canvas)."""
import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import assets, camera, db, groups, sprites, direction, director, jobs, llm, media, montage, promptgen, registry, sketch, storage, transitions, upscale, usage
from ..auth import current_user, need, require
from ..brand import CATEGORY_RULES
from ..config import MAX_UPLOAD_MB

router = APIRouter(prefix="/api")
files_router = APIRouter()


@router.get("/catalog")
def catalog(user: dict = Depends(current_user)):
    return {"camera": camera.catalog(), "transitions": transitions.catalog(),
            "categories": ["geral", *CATEGORY_RULES, "automotivo", "beleza"],
            "llm": {"online": llm.online(), "model": llm.source()}}


@router.get("/models")
def models(user: dict = Depends(current_user)):
    """Modelos que aparecem nos nós (habilitados no painel, com key e liberados para o usuário)."""
    rate = groups.token_rate()  # o estúdio só vê tokens, nunca o valor em dinheiro
    return {k: [{**registry.public(m), "cost": round(m["cost"] * rate, 3)} for m in registry.for_user(user, k)]
            for k in ("image", "video")}


def _tok_job(job: dict, rate: float | None = None) -> dict:
    rate = rate if rate is not None else groups.token_rate()
    job = {**job, "est_cost": round((job.get("est_cost") or 0) * rate, 2),
           "steps": [{**s, "est": round((s.get("est") or 0) * rate, 2), "cost": round((s.get("cost") or 0) * rate, 2)}
                     for s in job.get("steps", [])]}
    return job


# ---------- prompts / câmera / transições ----------

@router.post("/camera/analyze")
def analyze_camera(path: promptgen.CameraPath, user: dict = Depends(current_user)):
    return promptgen.analyze_path(path)


# ---------- personagens e produtos ----------

def _ws_asset(user: dict, kind: str, node: bool = True):
    require(user, "ws_product" if kind == "product" else "ws_character")
    if node:
        require(user, "node_prodgen" if kind == "product" else "node_chargen")


@router.get("/assets")
def list_assets(kind: str | None = None, user: dict = Depends(current_user)):
    return {"items": assets.list_for(user, kind), "views": assets.VIEWS}


@router.post("/assets")
def create_asset(body: assets.AssetBody, user: dict = Depends(current_user)):
    _ws_asset(user, body.kind)
    return assets.create(user, body)


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, user: dict = Depends(current_user)):
    return assets.get(asset_id, user)


@router.put("/assets/{asset_id}")
def update_asset(asset_id: str, body: assets.AssetBody, user: dict = Depends(current_user)):
    _ws_asset(user, assets.get(asset_id, user)["kind"])
    return assets.update(asset_id, user, body)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, user: dict = Depends(current_user)):
    assets.delete(asset_id, user)
    return {"ok": True}


@router.post("/assets/{asset_id}/save")
def save_asset(asset_id: str, body: assets.SaveBody, user: dict = Depends(need("node_save"))):
    return assets.save_final(asset_id, user, body)


@router.post("/assets/{asset_id}/generate")
async def generate_asset(asset_id: str, body: assets.GenerateBody, user: dict = Depends(current_user)):
    _ws_asset(user, (await assets.aget(asset_id, user))["kind"])
    out = await asyncio.to_thread(assets.generate, asset_id, user, body)
    return {**out, "jobs": [_tok_job(j) for j in out["jobs"]]}


class PosePromptBody(BaseModel):
    key: str
    extra: str = ""
    with_master: bool = True


@router.get("/assets/{asset_id}/directions")
def asset_directions(asset_id: str, user: dict = Depends(current_user)):
    a = assets.get(asset_id, user)
    return {"directions": [{**d, "prompt": sprites.pose_prompt(a, d["key"])} for d in sprites.directions(a["kind"])],
            "master_prompt": sprites.pose_prompt(a, "front", with_master=False)}


@router.post("/assets/{asset_id}/pose-prompt")
def pose_prompt(asset_id: str, body: PosePromptBody, user: dict = Depends(current_user)):
    return {"prompt": sprites.pose_prompt(assets.get(asset_id, user), body.key, body.extra, body.with_master)}


@router.post("/assets/{asset_id}/pose-render")
async def pose_render(asset_id: str, body: sprites.PoseRender, user: dict = Depends(current_user)):
    a = await assets.aget(asset_id, user)
    _ws_asset(user, a["kind"])
    try:
        out = await asyncio.to_thread(sprites.render_pose, user, a, body)
        return {**out, "job": _tok_job(out["job"], await groups.atoken_rate())}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/prompts/suggest")
async def suggest_prompts(body: sprites.SuggestBody, user: dict = Depends(need("ai_assist"))):
    return await asyncio.to_thread(sprites.suggest, body)


@router.post("/spritesheet")
async def spritesheet(body: sprites.SheetBody, user: dict = Depends(current_user)):
    if not (user["permissions"]["ws_character"] or user["permissions"]["ws_product"]):
        require(user, "ws_character")
    require(user, "node_sheet")
    await asyncio.to_thread(storage.check_quota, user)
    try:
        out = await asyncio.to_thread(sprites.build_sheet, user, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await usage.alog("montage", detail={"spritesheet": len(body.urls)})
    return out


@router.post("/image/regenerate-from-sketch")
async def regenerate_from_sketch(req: sketch.SketchRequest, user: dict = Depends(current_user)):
    """Regera a imagem do nó a partir das marcações: visão escreve a edição, o gerador recebe a imagem original."""
    if not sketch.active_layers(req):
        raise HTTPException(400, "Desenhe ou escreva pelo menos uma marcação")
    if not req.annotated.startswith("data:image/") or len(req.annotated) > 12 * 2**20:
        raise HTTPException(400, "Imagem com as marcações inválida ou grande demais")
    storage.resolve(req.source_url, user)  # a imagem de origem tem de ser do próprio usuário
    edit = await asyncio.to_thread(sketch.plan_edit, req)
    job = await asyncio.to_thread(jobs.prepare, user, sketch.build_request(req, edit))  # cotas, permissões, fila
    jobs.start(job, user)
    return {**_tok_job(job, await groups.atoken_rate()),
            "edit": {"prompt_en": edit["edit_en"], "notes_pt": edit["notes_pt"], "source": edit["source"]}}


@router.post("/image/upscale")
async def upscale_image(req: upscale.UpscaleRequest, user: dict = Depends(current_user)):
    """Upscale 2x/4x: entra na fila de jobs (cotas e tokens iguais aos demais renders)."""
    src = storage.resolve(req.source_url, user)  # imagem do próprio usuário
    try:
        w, h = await asyncio.to_thread(upscale.image_size, src)
    except Exception as e:  # noqa: BLE001 - arquivo que não é imagem
        raise HTTPException(400, "Arquivo de entrada não é uma imagem") from e
    prompt = upscale.prompt_for(req, w, h)
    rr = jobs.RenderRequest(node_id=req.node_id, name=req.name, gen="upscale", model=req.model, prompt=prompt,
                            keyframe_prompt=prompt, quality=req.quality, ref_image_urls=[req.source_url],
                            upscale_factor=req.factor)
    job = await asyncio.to_thread(jobs.prepare, user, rr)
    jobs.start(job, user)
    mode = "local" if job["steps"][0]["provider"] == "local" else "ai"
    return {**_tok_job(job, await groups.atoken_rate()), "mode": mode}


@router.post("/direction/interpret")
async def direction_interpret(req: direction.DirectionRequest, user: dict = Depends(need("ai_assist"))):
    """Frame com o desenho (roxo = câmera, cores = elementos) + notas -> plano de movimento."""
    if len(req.annotated) > 12 * 2**20:
        raise HTTPException(413, "Imagem do desenho grande demais")
    return await asyncio.to_thread(direction.interpret, req)


@router.post("/director/plan")
async def director_plan(req: director.PlanRequest, user: dict = Depends(need("ai_assist"))):
    require(user, "node_brief")
    return await asyncio.to_thread(director.plan, req)


@router.post("/autoprompt")
async def autoprompt(req: promptgen.AutoPromptRequest, user: dict = Depends(need("ai_assist"))):
    return await asyncio.to_thread(promptgen.autoprompt, req)


@router.post("/transition/plan")
async def plan_transition(req: transitions.PlanRequest, user: dict = Depends(need("node_transition"))):
    return await asyncio.to_thread(transitions.plan, req)


# ---------- render ----------

@router.post("/render")
async def render(req: jobs.RenderRequest, user: dict = Depends(current_user)):
    job = await asyncio.to_thread(jobs.prepare, user, req)  # checagens e banco fora do loop
    jobs.start(job, user)
    return _tok_job(job, await groups.atoken_rate())


@router.get("/jobs")
async def list_jobs(user: dict = Depends(current_user)):
    rate = await groups.atoken_rate()
    return [_tok_job(j, rate) for j in await jobs.alist_for(user)]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(current_user)):
    return _tok_job(await jobs.aget(job_id, user), await groups.atoken_rate())


class MontageRequest(BaseModel):
    items: list[dict] = Field(default_factory=list)  # [{type:clip,url,start,end} | {type:transition,kind,duration}]
    aspect: str = "16:9"
    audio_url: str | None = None


@router.post("/montage")
async def montage_cut(req: MontageRequest, user: dict = Depends(need("node_montage"))):
    await asyncio.to_thread(storage.check_quota, user)
    items = [{**i, "path": storage.resolve(i["url"], user)} if i.get("type") == "clip" else i for i in req.items]
    audio = storage.resolve(req.audio_url, user) if req.audio_url else None
    try:
        out = await asyncio.to_thread(montage.assemble_sequence, user["id"], items, req.aspect, audio)
    except (RuntimeError, ValueError) as e:
        await usage.alog("montage", status="error", detail=str(e)[:300])
        raise HTTPException(400, str(e)) from e
    await usage.alog("montage", detail={"clips": sum(1 for i in items if i.get("type") == "clip")})
    return {"url": storage.to_url(out), "shots": sum(1 for i in items if i.get("type") == "clip")}


# ---------- mídia ----------

@router.post("/upload")
async def upload(file: UploadFile, user: dict = Depends(need("upload"))):
    data = await file.read(MAX_UPLOAD_MB * 2**20 + 1)
    if len(data) > MAX_UPLOAD_MB * 2**20:
        raise HTTPException(413, f"Arquivo maior que {MAX_UPLOAD_MB} MB")
    await asyncio.to_thread(storage.check_quota, user, len(data))
    try:
        m = await asyncio.to_thread(media.save_upload, user["id"], file.filename or "arquivo", data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Falha ao processar mídia: {e}") from e
    await usage.alog("upload", detail={"file": m["name"], "bytes": len(data)})
    return m


class MediaRef(BaseModel):
    url: str


@router.post("/media/describe")
async def describe_media(ref: MediaRef, user: dict = Depends(current_user)):
    return await asyncio.to_thread(media.describe, storage.resolve(ref.url, user))


class FrameRequest(BaseModel):
    url: str
    t: float


@router.post("/media/frame")
async def frame_at(req: FrameRequest, user: dict = Depends(need("node_frame"))):
    path = storage.resolve(req.url, user)
    return {"url": storage.to_url(await asyncio.to_thread(media.extract_at, path, req.t)), "t": req.t}


@router.get("/files")
def my_files(user: dict = Depends(current_user)):
    idx = assets.files_index(user)
    files = [{**f, "asset": idx.get(f["url"])} for f in storage.list_files(user["id"])]
    return {"files": files, "used": storage.used_bytes(user["id"]), "limit_mb": user["limits"].get("storage_mb")}


class DeleteFiles(BaseModel):
    urls: list[str]


@router.post("/files/delete")
def delete_files(body: DeleteFiles, user: dict = Depends(need("delete_files"))):
    freed = sum(storage.delete(u, user) for u in body.urls)
    usage.log("delete", detail={"files": len(body.urls), "bytes": freed})
    return {"ok": True, "freed": freed}


# ---------- canvas ----------

WORKSPACES = {"video", "character", "product"}
# Abas: "video" é a principal; "video~ab12cd" são abas extras da mesma área.
TAB_RE = re.compile(r"^(video|character|product)(~[a-z0-9]{4,16})?$")


def _ws_key(user: dict, ws: str) -> str:
    m = TAB_RE.match(ws or "")
    if not m:
        raise HTTPException(400, "área inválida")
    require(user, f"ws_{m.group(1)}")
    return ws


@router.get("/canvas")
async def load_canvas(ws: str = "video", user: dict = Depends(current_user)):
    ws = _ws_key(user, ws)
    r = await db.aone("SELECT doc FROM canvases WHERE user_id = ? AND ws = ?", (user["id"], ws))
    return json.loads(r["doc"]) if r else {"nodes": [], "edges": []}


@router.post("/canvas")
async def save_canvas(doc: dict, ws: str = "video", user: dict = Depends(current_user)):
    ws = _ws_key(user, ws)
    raw = json.dumps(doc, ensure_ascii=False)
    if len(raw) > 5 * 2**20:
        raise HTTPException(413, "Canvas grande demais")
    # checagem do limite de abas e gravação na mesma transação (sem corrida entre duas abas novas)
    async with db.atx() as c:
        if "~" in ws and not await (await c.execute("SELECT 1 FROM canvases WHERE user_id = ? AND ws = ?", (user["id"], ws))).fetchone():
            cur = await c.execute("SELECT COUNT(*) n FROM canvases WHERE user_id = ? AND ws LIKE ?", (user["id"], ws.split("~")[0] + "~%"))
            if (await cur.fetchone())["n"] >= 30:
                raise HTTPException(400, "Limite de 30 abas por área")
        await c.execute("INSERT INTO canvases(user_id, ws, doc, updated) VALUES(?,?,?,?) ON CONFLICT(user_id, ws) DO UPDATE SET "
                        "doc = excluded.doc, updated = excluded.updated", (user["id"], ws, raw, db.now()))
    return {"ok": True}


@router.get("/canvas/tabs")
def canvas_tabs(user: dict = Depends(current_user)):
    """Abas de cada área: a principal sempre existe; as extras vêm do banco (título e ordem ficam no doc)."""
    out = {ws: [{"key": ws, "title": "", "created": 0}] for ws in WORKSPACES if user["permissions"].get(f"ws_{ws}")}
    for r in db.all_("SELECT ws, doc FROM canvases WHERE user_id = ?", (user["id"],)):
        base = r["ws"].split("~")[0]
        if base not in out:
            continue
        try:
            d = json.loads(r["doc"])
        except ValueError:
            d = {}
        tab = {"key": r["ws"], "title": str(d.get("title") or "")[:60], "created": d.get("created") or 0}
        if r["ws"] == base:
            out[base][0] = {**tab, "created": 0}
        else:
            out[base].append(tab)
    for tabs in out.values():
        tabs.sort(key=lambda t: (t["key"] != t["key"].split("~")[0], t["created"]))
    return out


@router.delete("/canvas")
def delete_canvas(ws: str, user: dict = Depends(current_user)):
    ws = _ws_key(user, ws)
    if "~" not in ws:
        raise HTTPException(400, "A aba principal não pode ser fechada")
    db.run("DELETE FROM canvases WHERE user_id = ? AND ws = ?", (user["id"], ws))
    return {"ok": True}


# ---------- arquivos protegidos ----------

@files_router.get("/files/{path:path}")
def serve_file(path: str, user: dict = Depends(current_user)):
    return FileResponse(storage.resolve(f"/files/{path}", user), headers={"Cache-Control": "private, max-age=3600"})
