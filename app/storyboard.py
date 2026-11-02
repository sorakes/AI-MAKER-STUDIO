"""Storyboard: briefing curto -> plano com N planos (imagem + vídeo + câmera) pronto para virar nós no canvas.

Só texto (modelo barato da administração). Sem modelo, o plano não sai. Nada é gerado aqui: a imagem e o
vídeo só são feitos depois, pelos nós, com o custo mostrado antes.
"""
from typing import Literal

from pydantic import BaseModel, Field

from . import camera, llm

IMAGE_USD = 0.034        # estimativa por imagem (modelo de imagem barato)
VIDEO_USD_PER_S = 0.09   # estimativa por segundo de vídeo (modelo de vídeo barato)
MIN_SHOT_S, MAX_SHOT_S = 2.0, 8.0
MAX_REFS = 5


class StoryboardError(Exception):
    pass


class Asset(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["personagem", "produto"] = "personagem"


class PlanRequest(BaseModel):
    brief: str = Field(min_length=10, max_length=400)
    shots: int = Field(default=4, ge=3, le=6)
    total: int = Field(default=15, ge=6, le=60)  # segundos somados dos planos
    aspect: Literal["16:9", "9:16"] = "16:9"
    style: str = Field(default="", max_length=80)
    assets: list[Asset] = Field(default_factory=list, max_length=20)  # inventário do usuário (personagens e produtos)


SYSTEM = (
    "You are a storyboard artist for short commercial videos. Reply ONLY with JSON: "
    '{"title": "short campaign title", "shots": [{"title": "2-5 words", "description": "what is seen and what moves, '
    '1-3 sentences", "image_prompt": "dense English prompt for the still of the first frame", "video_prompt": "English '
    'prompt for the motion of this shot", "duration": seconds, "camera": "one id from the list", "refs": ["recurring '
    'characters or products used in this shot, with the exact names given"]}]}. '
    "Rules: exactly the requested number of shots; durations between 2 and 8 seconds, adding up close to the requested "
    "total; the first shot is a hook; the last one is a closing shot; camera ids only from this list: {cams}; keep the "
    "same characters and products consistent across shots."
)


def _system() -> str:
    return SYSTEM.replace("{cams}", ", ".join(camera.MOVES))


def _user(req: PlanRequest) -> str:
    names = ", ".join(a.name for a in req.assets) or "none"
    return (f"Briefing: {req.brief}\nNumber of shots: {req.shots}\nTotal seconds: {req.total}\n"
            f"Format: {req.aspect}\nStyle: {req.style or 'free'}\nSaved characters and products: {names}")


def _clean(raw, canon: dict[str, str]) -> dict | None:
    """Normaliza um plano do modelo: texto obrigatório, duração entre 2 e 8 s, câmera só dos presets, refs com nome."""
    if not isinstance(raw, dict):
        return None
    text = {k: str(raw.get(k) or "").strip() for k in ("title", "description", "image_prompt", "video_prompt")}
    if not all(text.values()):
        return None
    try:
        dur = float(raw.get("duration") or 4)
    except (TypeError, ValueError):
        dur = 4.0
    move = raw.get("camera") if raw.get("camera") in camera.MOVES else "static"
    refs: list[str] = []
    for r in raw.get("refs") or []:
        name = str(r).strip()[:80]
        name = canon.get(name.lower(), name)
        if name and name not in refs:
            refs.append(name)
    return {"title": text["title"][:80], "description": text["description"][:400],
            "image_prompt": text["image_prompt"][:600], "video_prompt": text["video_prompt"][:600],
            "duration": round(min(max(dur, MIN_SHOT_S), MAX_SHOT_S), 1), "camera": move,
            "camera_hint": camera.MOVES[move]["prompt"], "refs": refs[:MAX_REFS]}


def _scale(shots: list[dict], total: int) -> None:
    """Ajusta as durações para somar perto do total pedido, mantendo os limites de cada plano."""
    s = sum(x["duration"] for x in shots)
    if s <= 0:
        return
    f = total / s
    for x in shots:
        x["duration"] = round(min(max(x["duration"] * f, MIN_SHOT_S), MAX_SHOT_S), 1)


def estimate(shots: list[dict]) -> dict:
    images = len(shots)
    seconds = round(sum(x["duration"] for x in shots), 1)
    return {"images": images, "video_seconds": seconds,
            "usd": round(images * IMAGE_USD + seconds * VIDEO_USD_PER_S, 2)}


def build_plan(req: PlanRequest) -> dict:
    raw = llm.chat_json(_system(), _user(req))
    if not isinstance(raw, dict):
        raise StoryboardError("Sem modelo de texto disponível para montar o storyboard")
    items = raw.get("shots")
    if not isinstance(items, list) or not items:
        raise StoryboardError("O modelo não devolveu planos. Tente de novo")
    canon = {a.name.lower(): a.name for a in req.assets}
    shots = [s for s in (_clean(x, canon) for x in items[:req.shots]) if s]
    if len(shots) < 2:
        raise StoryboardError("Plano incompleto. Tente de novo")
    _scale(shots, req.total)
    title = str(raw.get("title") or "Storyboard").strip()[:120] or "Storyboard"
    return {"title": title, "shots": shots, "estimate": estimate(shots)}
