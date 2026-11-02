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
import subprocess
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from . import extend, media, montage, storage

MAX_SECONDS = 10.0
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
    preserve_audio: bool = True


def segment_seconds(start: float, end: float) -> float:
    return round(end - start, 3)


def validate_segment(start: float, end: float, duration: float) -> None:
    """Erros de trecho, em português, para o endpoint devolver 400."""
    if end <= start:
        raise ValueError("O fim do trecho precisa ser depois do início")
    if end - start > MAX_SECONDS:
        raise ValueError(f"Edite no máximo {int(MAX_SECONDS)} s por vez (o trecho tem {end - start:.1f} s)")
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


def run(src: Path, op: dict, dest: Path, edit_frame) -> float:
    """Edita o trecho por quadros e remonta o vídeo. edit_frame(prompt, frame_url) -> (data_uri, custo).
    Devolve o custo real somado dos quadros."""
    start, end = float(op["start"]), float(op["end"])
    info = media.probe(src)
    validate_segment(start, end, info["duration"])
    end = min(end, info["duration"])
    size = (int(info["width"]), int(info["height"]))
    n = frame_count(end - start)
    work = dest.parent / f"{dest.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    cost, clips = 0.0, []
    seg_each = (end - start) / n
    text = instruction(VideoEditRequest(source_url="x", prompt=op.get("prompt", ""), preset=op["preset"],
                                        strength=op["strength"]))
    for k, t in enumerate(_frame_times(start, end, n)):
        raw = work / f"f{k}.png"
        _extract(src, t, raw)
        frame_url = storage.to_url(raw)
        uri, c = edit_frame(text, frame_url)
        cost += c or 0.0
        edited = work / f"e{k}.png"
        _save_data_uri(uri, edited, size)
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
    if op.get("preserve_audio", True) and extend.has_audio(src):
        audio_path = work / "audio.mp3"
        _extract_audio(src, audio_path)
    aspect = "16:9" if size[0] >= size[1] else "9:16"
    joined = montage.assemble_sequence(op["user_id"], items, aspect, audio_path)
    joined.replace(dest)
    return cost
