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
