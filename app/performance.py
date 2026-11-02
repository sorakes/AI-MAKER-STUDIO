"""Performance: o personagem da imagem fala com expressão (modelo de vídeo com áudio gerado).

Verificado na API real (validação, sem geração):
- heygen/avatar-iv aceita imagem e áudio, mas o áudio precisa de URL HTTPS pública: não usamos.
- O áudio em data URL é recusado por todos os modelos ("Only HTTPS URLs are allowed").
- Então o modelo fala o TEXTO com voz própria (generate_audio). O áudio ligado, se houver, só mede a duração.
  Por isso a fala gerada não sincroniza com um áudio enviado; a UI diz isso.
"""
import math
from typing import Literal

from pydantic import BaseModel, Field

PERF_MODEL_ID = "bytedance/seedance-2.0-fast"  # o mais barato da lista que fala com áudio gerado e tem 480p
MIN_SECONDS, MAX_SECONDS = 4, 15
CHARS_PER_SECOND = 14


class PerformanceRequest(BaseModel):
    node_id: str = ""
    name: str = "Performance"
    image_url: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=300)
    motion: str = Field(default="", max_length=200)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    audio_url: str | None = None  # só para medir a duração (o modelo não recebe o áudio)


def seconds_for(text: str, audio_seconds: float | None = None) -> int:
    """Duração inteira aceita pelo modelo: a do áudio ligado (arredondada para cima), senão estimada pelo texto."""
    base = audio_seconds if audio_seconds else max(1.0, len(text.strip()) / CHARS_PER_SECOND)
    return int(min(MAX_SECONDS, max(MIN_SECONDS, math.ceil(base))))


def prompt_for(req: PerformanceRequest) -> str:
    return (f'The person in the image speaks to the camera, saying: "{req.text.strip()}". Natural lip movement '
            "synced to the speech, expressive face, small natural head motion. "
            f"{req.motion.strip()} Keep the face, clothes and background identical.").strip()


def find_model(models: list[dict]) -> dict | None:
    """O modelo de performance, se estiver liberado para o usuário e disponível."""
    return next((m for m in models if m.get("model_id") == PERF_MODEL_ID and m.get("available", True)), None)


FRAME_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}


def fit_frame(src, aspect: str, dest):
    """Quadro inicial no formato pedido. O modelo segue o formato da imagem inicial (e não o aspect_ratio),
    então a imagem é encaixada inteira no quadro, com o fundo desfocado da própria imagem (sem cortar o rosto)."""
    from PIL import Image, ImageFilter, ImageOps

    w, h = FRAME_SIZES[aspect]
    img = Image.open(src).convert("RGB")
    bg = ImageOps.fit(img, (w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    scale = min(w / img.width, h / img.height)
    fg = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    bg.save(dest, format="PNG")
    return dest


def needs_fit(size: tuple[int, int], aspect: str, tolerance: float = 0.02) -> bool:
    """A imagem já está no formato pedido? (diferença de proporção maior que a tolerância)."""
    w, h = FRAME_SIZES[aspect]
    return abs(size[0] / size[1] - w / h) > tolerance
