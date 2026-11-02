"""Upscale criativo: imagem -> imagem maior (2x ou 4x).

Dois caminhos, ambos no fluxo normal de jobs (cotas, tokens, fila e versões):
- com um modelo de imagem que aceite referências (ex.: OpenRouter): uma passada de IA refina a imagem
  (criatividade = quanto acrescenta, semelhança = quanto mantém) e depois o arquivo é levado ao fator exato;
- sem esse modelo: upscale simples local (Pillow LANCZOS + nitidez leve). A UI avisa que é simples.
"""
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFilter
from pydantic import BaseModel, Field

from . import registry

LOCAL_ID = "local"
LOCAL = {"id": LOCAL_ID, "kind": "image", "label": "Upscale simples (Pillow)", "provider": "local",
         "model_id": "pillow-lanczos", "caps": {}, "fields": {}, "params": {}, "cost": 0.0, "cost_unit": "run",
         "key_id": None, "key_name": "", "is_default": 0, "enabled": 1}


class UpscaleRequest(BaseModel):
    node_id: str = ""
    name: str = "Upscale"
    source_url: str  # imagem de entrada (/files/...): é a referência e o que será ampliado
    factor: Literal[2, 4] = 2
    creativity: int = Field(30, ge=0, le=100)  # quanto a IA acrescenta de detalhe
    similarity: int = Field(70, ge=0, le=100)  # quanto deve permanecer igual ao original
    prompt: str = Field("", max_length=1000)  # nota opcional do usuário
    model: str | int | None = "auto"
    quality: str = "draft"


def pick(user: dict, choice) -> dict | None:
    """Modelo com referências para a passada de IA; None = upscale simples local."""
    if choice == LOCAL_ID:
        return None
    return registry.pick(user, "image", choice, {"refs": True})


def _tone_similarity(v: int) -> str:
    if v >= 70:
        return "keep the composition, subject, colors and every detail identical to image 1"
    if v >= 40:
        return "keep the subject and composition of image 1; small changes to detail are allowed"
    return "reimagine freely, keeping only the general subject of image 1"


def _tone_creativity(v: int) -> str:
    if v >= 70:
        return "add rich, realistic micro-detail, texture and sharpness"
    if v >= 30:
        return "add moderate fine detail and crisp texture"
    return "only sharpen and clean up what is already there, adding nothing new"


def prompt_for(req: UpscaleRequest, w: int, h: int) -> str:
    """Prompt da passada de IA. A imagem 1 é o original; nenhum traço deve aparecer."""
    tw, th = w * req.factor, h * req.factor
    note = f" Extra note from the artist: {req.prompt.strip()}." if req.prompt.strip() else ""
    return (f"Upscale image 1 to {tw}x{th} pixels. {_tone_similarity(req.similarity).capitalize()}; "
            f"{_tone_creativity(req.creativity)}.{note} The result must be a clean high-resolution photograph with "
            "NO marks, labels or annotations of any kind.")


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def resize_file(src: Path, factor: int, dest: Path | None = None) -> Path:
    """Upscale local: Lanczos até o fator exato + nitidez leve. Grava em `dest` (ou sobrescreve `src`)."""
    if factor not in (2, 4):
        raise ValueError("fator deve ser 2 ou 4")
    dest = dest or src
    with Image.open(src) as im:  # copia e fecha antes de gravar (pode ser o mesmo arquivo)
        fmt = im.format or "PNG"
        base = im.copy()
    if fmt in ("JPEG", "WEBP") and base.mode not in ("RGB", "RGBA"):
        base = base.convert("RGB")
    big = base.resize((base.width * factor, base.height * factor), Image.Resampling.LANCZOS)
    big = big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    big.save(dest, format=fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return dest
