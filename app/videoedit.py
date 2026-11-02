"""Editar vídeo existente, por quadros (fallback honesto).

Por que quadros: o modelo de edição de vídeo (FLUX Video Edit) exige o vídeo de entrada em uma URL HTTPS pública.
O app roda localmente e o provedor não consegue buscar o arquivo, então a edição é feita assim:
  1. extrai um quadro por segundo do trecho (2 a 6 quadros);
  2. edita cada quadro com o modelo de imagem barato (instrução + quadro de referência);
  3. remonta: cada quadro editado dura o seu pedaço do trecho; o resto do vídeo volta original;
  4. o áudio original entra por cima, para manter a sincronia.
A aproximação aparece na UI: quadros editados separados por corte podem tremer e perder continuidade.
"""
import base64
import io
import math
import os
import subprocess
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from . import media, montage, storage, tunnel

MAX_SECONDS = 10.0  # por quadros
NATIVE_MODEL = os.environ.get("VIDEO_EDIT_MODEL", "black-forest-labs/flux-video-edit")
NATIVE_USD_PER_S = float(os.environ.get("VIDEO_EDIT_USD_PER_S", "0.03"))  # preço de tabela; o custo real vem do job
NATIVE_CHUNK_S = 14.0  # o modelo aceita até 15 s por clipe: vídeos maiores são editados em pedaços
NATIVE_MAX_SECONDS = 120.0  # teto por pedido (custo e tempo)
MIN_FRAMES, MAX_FRAMES = 2, 6
PRESETS = {
    "none": "",
    "anime": "restyle the frame as an anime illustration",
    "night": "turn the day into night, moonlit, street lamps on",
    "sunset": "replace the sky with a golden sunset",
    "background": "replace the background with a different environment, keep the main subject",
    "color": "change the color of the main element",
}
STRENGTH = {"low": "change it subtly", "medium": "change it clearly", "high": "change it strongly"}


class VideoEditRequest(BaseModel):
    node_id: str = ""
    name: str = "Editar vídeo"
    source_url: str = Field(min_length=1, max_length=512)
    prompt: str = Field(default="", max_length=300)
    preset: Literal["none", "anime", "night", "sunset", "background", "color"] = "none"
    strength: Literal["low", "medium", "high"] = "medium"
    start: float = Field(default=0.0, ge=0)
    end: float = Field(default=3.0, gt=0)
    mode: Literal["swap", "motion"] = "swap"  # swap: troca personagem/roupa/objeto e mantém o resto; motion: só o movimento
    ref_urls: list[str] = Field(default_factory=list, max_length=8)  # fotos do personagem, roupa ou produto que entram
    whole: bool = False  # o vídeo inteiro: início 0 e fim = duração (o servidor mede)
    model: str = Field(default="auto", max_length=40)  # "auto", "frames" (por quadros) ou o id de um modelo nativo
    preserve_audio: bool = True


def segment_seconds(start: float, end: float) -> float:
    return round(end - start, 3)


# Editores de vídeo dedicados (entram sempre na lista). `min_usd`: mínimo cobrado por pedaço pelo provedor.
CATALOG = [
    {"id": "flux", "label": "FLUX Video Edit", "model_id": NATIVE_MODEL, "usd_per_s": NATIVE_USD_PER_S, "chunk_s": 14.8,
     "min_usd": 0.0, "generic": False},
    {"id": "aleph", "label": "Runway Aleph 2.0 (editor dedicado, caro)", "model_id": "runway/aleph-2", "usd_per_s": 0.28,
     "chunk_s": 10.0, "min_usd": 0.56, "generic": False},
    # Wan 2.7: recebe vídeo (parâmetros "video/videos" na OpenRouter), clipes de 2 a 10 s, US$ 0,10/s. Sem cadastro no
    # painel: o pedido leva duração, proporção e resolução daqui. Experimental (formato do vídeo de entrada não documentado).
    {"id": "wan", "label": "Wan 2.7 (experimental)", "model_id": "alibaba/wan-2.7", "usd_per_s": 0.10, "chunk_s": 10.0,
     "min_usd": 0.0, "generic": True, "durations": [2, 3, 4, 5, 6, 7, 8, 9, 10],
     "aspects": ["16:9", "9:16", "1:1", "4:3", "3:4"], "resolution": "720p"},
]


def catalog_entry(model_id: str) -> dict | None:
    return next((c for c in CATALOG if c["model_id"] == model_id), None)


# Modelos que RECEBEM VÍDEO como entrada (lista oficial /api/v1/videos/models da OpenRouter, 02/11/2026: preço
# "video_tokens_with_video_input" ou parâmetro "video/videos"). Modelos só texto/imagem→vídeo nunca entram neste node.
VIDEO_INPUT_MODELS = {"bytedance/seedance-2.0", "bytedance/seedance-2.0-fast", "bytedance/seedance-2.0-mini",
                      "bytedance/seedance-2.5", "alibaba/wan-2.7"}


def native_models(user: dict) -> list[dict]:
    """Modelos deste node: SÓ os que recebem o vídeo como entrada. Os editores dedicados (FLUX, Runway Aleph) e, dos
    modelos liberados no painel, os da VIDEO_INPUT_MODELS (Seedance 2.x, Wan 2.7), esses como experimentais: a
    OpenRouter lista entrada de vídeo para eles, mas não documenta o formato. Só valem com o túnel público ligado."""
    from . import registry  # import tardio: registry não pode depender deste módulo
    out = [{**c, "key_id": None, "reg_id": None} for c in CATALOG]
    known = {c["model_id"] for c in CATALOG}
    for m in registry.for_user(user, "video"):
        if m["model_id"] in known or m["model_id"] not in VIDEO_INPUT_MODELS:
            continue  # já listado, ou não recebe vídeo (só texto/imagem → vídeo): não serve para editar
        per_s = m["cost"] if m.get("cost_unit") == "second" and m.get("cost") else NATIVE_USD_PER_S
        out.append({"id": f"m{m['id']}", "label": f"{m['label']} (experimental)", "model_id": m["model_id"],
                    "key_id": m.get("key_id"), "usd_per_s": per_s, "chunk_s": NATIVE_CHUNK_S, "min_usd": 0.0,
                    "generic": True, "reg_id": m["id"]})
    return out


def choose_model(user: dict, sel: str) -> dict | None:
    """Modelo escolhido; sempre um que recebe o vídeo. 'auto' = FLUX. ValueError em português (vira 400)."""
    sel = (sel or "auto").strip()
    if sel == "frames":
        raise ValueError("Este node só usa modelos que recebem o vídeo. Escolha um modelo da lista.")
    if not tunnel.available():
        raise ValueError("O túnel público está desligado: o modelo precisa dele para receber o vídeo. "
                         "Suba o serviço 'tunnel' do docker-compose.")
    models = native_models(user)
    pick = models[0] if sel == "auto" else next((m for m in models if m["id"] == sel), None)
    if not pick:
        raise ValueError("Modelo de edição de vídeo desconhecido")
    return pick


def native_available() -> bool:
    """Edição de vídeo inteira: precisa do túnel público (o provedor baixa o vídeo por um link https)."""
    return tunnel.available()


def validate_segment(start: float, end: float, duration: float, native: bool = False) -> None:
    """Erros de trecho, em português, para o endpoint devolver 400."""
    if end <= start:
        raise ValueError("O fim do trecho precisa ser depois do início")
    cap = NATIVE_MAX_SECONDS if native else MAX_SECONDS
    if end - start > cap:
        raise ValueError(f"Edite no máximo {int(cap)} s por vez (o trecho tem {end - start:.1f} s)")
    if start >= duration:
        raise ValueError("O início do trecho está depois do fim do vídeo")


def frame_count(seconds: float) -> int:
    return min(MAX_FRAMES, max(MIN_FRAMES, round(seconds)))


def instruction(req: VideoEditRequest) -> str:
    """Instrução de edição para cada quadro: preset, texto do usuário e força."""
    parts = [PRESETS[req.preset], req.prompt.strip()]
    text = ". ".join(p for p in parts if p)
    return (f"Edit this video frame: {text or 'make the requested edit'}. {STRENGTH[req.strength]}. "
            "Keep everything else identical: framing, people, lighting and style not mentioned above.")


def estimate(seconds: float, image_usd: float) -> dict:
    n = frame_count(seconds)
    usd = round(n * image_usd, 4)
    return {"frames": n, "usd": usd}


def _frame_times(start: float, end: float, n: int) -> list[float]:
    """Quadros no meio de cada pedaço do trecho (nunca exatamente no último instante)."""
    step = (end - start) / n
    return [round(start + step * (i + 0.5), 3) for i in range(n)]


def _extract(src: Path, t: float, out: Path) -> None:
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-ss", str(t), "-i", str(src), "-frames:v", "1",
                    str(out)], check=True, capture_output=True, timeout=120)


def _save_data_uri(uri: str, out: Path, size: tuple[int, int]) -> None:
    """Imagem editada (data URI do provedor) -> PNG no tamanho do vídeo de origem."""
    raw = base64.b64decode(uri.split(",", 1)[1]) if uri.startswith("data:") else b""
    if not raw:
        raise ValueError("o modelo não devolveu a imagem do quadro")
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    img.save(out, format="PNG")


def _still_clip(png: Path, seconds: float, out: Path) -> None:
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-t",
                    f"{seconds:.3f}", "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True, timeout=300)


def _extract_audio(src: Path, out: Path) -> None:
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-i", str(src), "-vn", "-c:a", "libmp3lame",
                    "-b:a", "128k", str(out)], check=True, capture_output=True, timeout=300)


CHAIN_FRAME = ' Image 2 is the PREVIOUS frame of this same video, already edited: keep exactly the same edited look (same character, outfit, colors, style) so the sequence stays consistent.'
REFS_FRAME = (" The other reference images show the new character, outfit or object: reproduce it exactly "
              "(same face, hair, clothing, colors, shape).")
CHAIN_NATIVE = (" The LAST attached reference image shows the target look of the edited result: the subject must look "
                "exactly like it (same character, outfit, colors, style) in every frame of this clip.")
# Palavras que o FLUX Video Edit trata como pedido de extensão e recusa (HTTP 422): nunca no prompt de edição.
FORBIDDEN_EDIT_WORDS = ("continue", "extend", "previous part", "next part", "continuation")


def run(src: Path, op: dict, dest: Path, edit_frame) -> float:
    """Edita o trecho por quadros e remonta o vídeo. edit_frame(prompt, frame_url, prev_edited_url|None) -> (data_uri, custo).
    Em corrente: cada quadro recebe o anterior já editado como referência.
    Devolve o custo real somado dos quadros."""
    start, end = float(op["start"]), float(op["end"])
    info = media.probe(src)
    validate_segment(start, end, info["duration"])
    end = min(end, info["duration"])
    size = (int(info["width"]), int(info["height"]))
    n = frame_count(end - start)
    work = dest.parent / f"{dest.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    cost, clips, prev_url = 0.0, [], None  # prev_url: o quadro anterior já editado (continuidade)
    seg_each = (end - start) / n
    text = instruction(VideoEditRequest(source_url="x", prompt=op.get("prompt", ""), preset=op["preset"],
                                        strength=op["strength"]))
    for k, t in enumerate(_frame_times(start, end, n)):
        raw = work / f"f{k}.png"
        _extract(src, t, raw)
        frame_url = storage.to_url(raw)
        shown = text + (CHAIN_FRAME if prev_url else "") + (REFS_FRAME if op.get("ref_urls") else "")
        uri, c = edit_frame(shown, frame_url, prev_url)
        cost += c or 0.0
        edited = work / f"e{k}.png"
        _save_data_uri(uri, edited, size)
        prev_url = storage.to_url(edited)
        clip = work / f"c{k}.mp4"
        _still_clip(edited, seg_each, clip)
        clips.append({"type": "clip", "path": clip})
    items: list[dict] = []
    if start > 0.05:
        items.append({"type": "clip", "path": src, "start": 0, "end": start})
    items += clips
    if end < info["duration"] - 0.05:
        items.append({"type": "clip", "path": src, "start": end, "end": info["duration"]})
    audio_path = None
    if op.get("preserve_audio", True) and media.has_audio(src):
        audio_path = work / "audio.mp3"
        _extract_audio(src, audio_path)
    aspect = "16:9" if size[0] >= size[1] else "9:16"
    joined = montage.assemble_sequence(op["user_id"], items, aspect, audio_path)
    joined.replace(dest)
    return cost


# ---------- edição nativa: o vídeo inteiro (ou o trecho) vai ao modelo de edição de vídeo ----------

def native_chunks(start: float, end: float, chunk_s: float = NATIVE_CHUNK_S) -> list[tuple[float, float]]:
    """Divide o trecho em pedaços iguais de até chunk_s (cada modelo tem o seu limite de clipe)."""
    n = max(1, math.ceil((end - start) / chunk_s - 1e-9))
    step = (end - start) / n
    return [(round(start + step * i, 3), round(start + step * (i + 1), 3)) for i in range(n)]


PREFIX_S = 1.0  # emenda: segundos finais JÁ EDITADOS do pedaço anterior colados na frente do próximo
TAIL_HOLD_S = 0.5  # sobra no fim até este tamanho: segura o último quadro editado em vez de abrir outro pedaço
PREFIX_NOTE = (" The opening second of this clip is already edited: make the rest of the clip match it exactly "
               "(same subject, size, look and position).")


def chain_plan(start: float, end: float, chunk_s: float) -> tuple[list[tuple[float, float]], float]:
    """Pedaços do vídeo original e quantos segundos finais ficam com o último quadro editado segurado.
    Do 2º pedaço em diante o envio leva PREFIX_S do resultado anterior na frente, então cabe menos vídeo novo."""
    total = end - start
    if total <= chunk_s + 1e-9:
        return [(start, end)], 0.0
    if total - chunk_s <= TAIL_HOLD_S:  # ex.: 15,04 s no FLUX -> 1 pedaço só, sem emenda nenhuma
        return [(start, round(start + chunk_s, 3))], round(total - chunk_s, 3)
    n = max(2, math.ceil(total / (chunk_s - PREFIX_S) - 1e-9))
    step = total / n
    return [(round(start + step * i, 3), round(start + step * (i + 1), 3)) for i in range(n)], 0.0


def estimate_native(seconds: float, usd_per_s: float | None = None, chunk_s: float | None = None,
                    min_usd: float = 0.0) -> dict:
    """Custo = soma dos pedaços enviados (cada emenda paga também o PREFIX_S colado na frente); cada pedaço paga o
    mínimo do modelo (ex.: Runway cobra no mínimo US$ 0,56 por geração)."""
    rate = NATIVE_USD_PER_S if usd_per_s is None else usd_per_s
    ch, _ = chain_plan(0, seconds, chunk_s or NATIVE_CHUNK_S)
    billed = [(b - a) + (PREFIX_S if k else 0.0) for k, (a, b) in enumerate(ch)]
    return {"chunks": len(ch), "usd": round(sum(max(x * rate, min_usd) for x in billed), 4), "native": True}


def _upload_size(info: dict) -> tuple[int, int]:
    """Tamanho dos clipes enviados: o do vídeo, até 1280 px no lado maior, sempre par."""
    w, h = int(info["width"]), int(info["height"])
    s = min(1.0, 1280 / max(w, h))
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-an"]


def _cut_for_upload(src: Path, start: float, end: float, out: Path, size: tuple[int, int] | None = None) -> None:
    """Pedaço do vídeo pronto para subir: h264, até 1280 px (o modelo reduz acima de 720p; limite de 50 MiB)."""
    vf = (f"scale={size[0]}:{size[1]},setsar=1" if size else
          "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2")
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t",
                    f"{end - start:.3f}", "-vf", vf, *_ENC, str(out)], check=True, capture_output=True, timeout=600)


def _cut_with_prefix(prev: Path, src: Path, start: float, end: float, out: Path, size: tuple[int, int]) -> float:
    """Emenda: o último PREFIX_S do pedaço anterior JÁ EDITADO + o próximo pedaço original, num clipe só. O modelo vê o
    resultado pronto no começo e mantém o mesmo personagem/roupa. Devolve a duração real do prefixo (para cortar depois)."""
    w, h = size
    norm = f"scale={w}:{h},setsar=1,fps=25,format=yuv420p"
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-sseof", f"-{PREFIX_S:.3f}", "-i", str(prev),
                    "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src), "-filter_complex",
                    f"[0:v]{norm}[p];[1:v]{norm}[q];[p][q]concat=n=2:v=1:a=0[v]", "-map", "[v]", *_ENC, str(out)],
                   check=True, capture_output=True, timeout=600)
    return max(0.0, round(media.probe(out)["duration"] - (end - start), 3))


def _last_frame(clip: Path, out: Path) -> None:
    """Último quadro de um vídeo (PNG): `-update 1` regrava a saída a cada quadro e fica com o final."""
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-sseof", "-0.4", "-i", str(clip), "-update", "1",
                    "-q:v", "2", str(out)], check=True, capture_output=True, timeout=120)


KEEP_OBJECTS = (" Do not remove, replace or alter any other object, prop, tool or device, including ones that only "
                "appear partway through the clip: they must stay exactly as in the source, at the same time and place.")


def native_instruction(op: dict) -> str:
    """Instrução para o modelo de vídeo. swap: muda só o pedido e mantém o resto; motion: guarda movimento, tempo e câmera
    e refaz sujeito e cena. As imagens de referência (se houver) definem como o personagem/roupa/objeto novo deve ser."""
    parts = [PRESETS.get(op.get("preset", "none"), ""), (op.get("prompt") or "").strip()]
    text = ". ".join(p for p in parts if p)
    n = len(op.get("ref_urls") or [])
    refs = (f" Reference image{'s' if n > 1 else ''} 1{f'-{n}' if n > 1 else ''} show{'' if n > 1 else 's'} the new "
            "character, outfit or object: reproduce it exactly (same face, hair, clothing, colors, shape, labels).") if n else ""
    if op.get("mode") == "motion":
        base = (f"Keep ONLY the motion, timing and camera work of this video and rebuild the subject and the scene"
                f"{': ' + text if text else ' as shown in the references'}.")
    else:
        base = (f"Edit this video: {text or 'apply the change shown in the references'}. Change only that; keep everything "
                "else identical: motion, timing, camera, background, lighting and anything not mentioned." + KEEP_OBJECTS)
    return f"{base}{refs} {STRENGTH[op.get('strength', 'medium')]}."


def run_native(src: Path, op: dict, dest: Path, edit_clip) -> float:
    """Edita o trecho com o modelo de edição de vídeo, em corrente: os pedaços vão em SEQUÊNCIA e cada um recebe o último
    quadro já editado do anterior (edit_clip(prompt, clip_url, ref_data_uri|None) -> (bytes do mp4, custo)),
    para personagem, roupa e estilo não mudarem de um pedaço para o outro.
    Cada pedaço ganha um link público próprio que morre assim que o resultado chega (ou se algo falhar).
    Fora do trecho o vídeo volta original; o áudio original entra por cima para manter a sincronia."""
    start, end = float(op["start"]), float(op["end"])
    info = media.probe(src)
    validate_segment(start, end, info["duration"], native=True)
    end = min(end, info["duration"])
    work = dest.parent / f"{dest.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    text = native_instruction(op)
    if 0.05 < info["duration"] - end < 0.3:  # sobra mínima depois do trecho: entra no "segura o último quadro"
        end = info["duration"]
    chunks, hold = chain_plan(start, end, float(op.get("chunk_s") or NATIVE_CHUNK_S))
    op["_aspect"] = "16:9" if info["width"] >= info["height"] else "9:16"
    size = _upload_size(info)

    done: list[tuple[Path, float, float]] = []  # (clipe editado, custo, segundos de emenda na frente)
    ref: str | None = None  # último quadro editado do pedaço anterior (data URI): vai como imagem, se o modelo usar
    for k, (a, b) in enumerate(chunks):
        raw, edited, last = work / f"n{k}.mp4", work / f"ne{k}.mp4", work / f"last{k}.png"
        if done:  # emenda em vídeo: funciona até com modelos que ignoram imagens (ex.: FLUX)
            pre = _cut_with_prefix(done[-1][0], src, a, b, raw, size)
        else:
            _cut_for_upload(src, a, b, raw, size)
            pre = 0.0
        op["_chunk_len"] = (b - a) + pre  # os modelos genéricos precisam da duração do clipe enviado
        with tunnel.shared(raw) as url:  # o link existe só enquanto o provedor trabalha neste pedaço
            try:
                data, c = edit_clip(text + PREFIX_NOTE if pre else text, url, ref)
            except Exception as e:  # o que já foi editado foi cobrado: o job registra esse custo real
                e.partial_cost = sum(x for _, x, _ in done)
                raise
        edited.write_bytes(data)
        done.append((edited, c or 0.0, pre))
        if k < len(chunks) - 1 or hold:
            _last_frame(edited, last)
            ref = media.data_url(last)
    cost = sum(c for _, c, _ in done)
    items: list[dict] = []
    if start > 0.05:
        items.append({"type": "clip", "path": src, "start": 0, "end": start})
    # tira a emenda da frente e corta no tamanho do pedaço original (o modelo pode devolver um clipe mais longo)
    items += [{"type": "clip", "path": p, "start": pre, "end": pre + (b - a)} for (p, _, pre), (a, b) in zip(done, chunks)]
    if hold:  # sobra curta no fim: o último quadro editado segurado (sem voltar ao original nem abrir outro pedaço)
        still = work / "hold.mp4"
        _still_clip(work / f"last{len(chunks) - 1}.png", hold, still)
        items.append({"type": "clip", "path": still})  # o clipe inteiro (a montagem não corta trechos < 0,3 s)
    if end < info["duration"] - 0.05:
        items.append({"type": "clip", "path": src, "start": end, "end": info["duration"]})
    audio_path = None
    if op.get("preserve_audio", True) and media.has_audio(src):
        audio_path = work / "audio.mp3"
        _extract_audio(src, audio_path)
    aspect = "16:9" if info["width"] >= info["height"] else "9:16"
    joined = montage.assemble_sequence(op["user_id"], items, aspect, audio_path)
    joined.replace(dest)
    return cost
