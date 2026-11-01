"""Planejador de transições entre dois planos (A -> B).

Duas camadas:
  1. Checagens determinísticas de continuidade: direção do movimento de câmera
     (A termina indo para X, B começa indo para Y), salto de luz/cor entre o
     frame de saída de A e o frame de entrada de B, ponto de corte.
  2. O modelo de texto recebe as legendas visuais dos dois frames (modelo de visão),
     os movimentos de câmera e as checagens, e devolve o plano de transição em
     português + prompt para a ponte gerada + dica de câmera para abrir B.
"""
from PIL import Image, ImageStat
from pydantic import BaseModel, Field

from fastapi import HTTPException

from . import llm, prompts, storage
from .brand import BrandKit
from .montage import TRANSITIONS
from .promptgen import caption_for

# Vetor de movimento (x: direita+, y: cima+, z: aproximação+) por movimento analisado.
MOVE_VECTORS = {
    "pan_right": (1, 0, 0), "pan_left": (-1, 0, 0), "truck_right": (1, 0, 0), "truck_left": (-1, 0, 0),
    "whip_pan": (1, 0, 0), "orbit": (1, 0, 0),
    "dolly_in": (0, 0, 1), "push_in": (0, 0, 1), "zoom_in": (0, 0, 1), "crash_zoom": (0, 0, 1), "dolly_zoom": (0, 0, 1),
    "dolly_out": (0, 0, -1), "zoom_out": (0, 0, -1),
    "crane_up": (0, 1, 0), "tilt_up": (0, 1, 0), "tilt_down": (0, -1, 0), "crane_down": (0, -1, 0),
    "roll": (0, 0, 0), "static": (0, 0, 0), "handheld": (0, 0, 0),
}
DIR_PT = {(1, 0, 0): "direita", (-1, 0, 0): "esquerda", (0, 0, 1): "aproximando", (0, 0, -1): "afastando",
          (0, 1, 0): "subindo", (0, -1, 0): "descendo", (0, 0, 0): "parada"}


class ShotSide(BaseModel):
    name: str = ""
    intent: str = ""
    prompt: str | None = None
    move: str | None = None
    frame_url: str | None = None  # A: frame de saída (ponto de corte) / B: frame de entrada
    duration: float | None = None
    cut_at: float | None = None


class PlanRequest(BaseModel):
    a: ShotSide
    b: ShotSide
    kind: str = "auto"
    idea: str = ""  # ideia do diretor para a passagem de câmera (pt-BR, opcional)
    duration: float = 3.0  # duração da ponte gerada
    kit: BrandKit = Field(default_factory=BrandKit)


def _frame_stats(url: str | None) -> dict | None:
    if not url:
        return None
    try:
        path = storage.resolve(url)
    except HTTPException:
        return None
    img = Image.open(path).convert("RGB").resize((64, 36))
    r, g, b = ImageStat.Stat(img).mean
    return {"rgb": (r, g, b), "luma": 0.2126 * r + 0.7152 * g + 0.0722 * b}


def checks(req: PlanRequest) -> list[dict]:
    out = []
    va, vb = MOVE_VECTORS.get(req.a.move or "static"), MOVE_VECTORS.get(req.b.move or "static")
    if va and vb:
        da, db = DIR_PT.get(va, "?"), DIR_PT.get(vb, "?")
        opposite = any(x * y < 0 for x, y in zip(va, vb))
        same = va == vb and va != (0, 0, 0)
        if opposite:
            out.append({"ok": False, "text": f"Movimento invertido: A termina {da}, B começa {db}. "
                                             "Quebra a continuidade; use corte seco/flash ou inverta a câmera de B."})
        elif same:
            out.append({"ok": True, "text": f"Movimento contínuo: A e B andam {da}. Ideal para whip pan ou match cut."})
        else:
            out.append({"ok": True, "text": f"A termina {da}, B começa {db}: mudança de eixo aceitável com corte motivado."})
    sa, sb = _frame_stats(req.a.frame_url), _frame_stats(req.b.frame_url)
    if sa and sb:
        dl = sb["luma"] - sa["luma"]
        dc = sum(abs(x - y) for x, y in zip(sa["rgb"], sb["rgb"])) / 3
        if abs(dl) > 45 or dc > 50:
            out.append({"ok": False, "text": f"Salto grande de luz/cor (Δluz {dl:+.0f}, Δcor {dc:.0f}). "
                                             "Dissolve, fade ou flash escondem melhor que corte seco."})
        else:
            out.append({"ok": True, "text": f"Luz e cor próximas (Δluz {dl:+.0f}, Δcor {dc:.0f}): corte limpo funciona."})
    elif not req.b.frame_url:
        out.append({"ok": False, "text": "B ainda não tem vídeo: gere B para a transição saber onde chegar."})
    if req.a.duration and req.a.cut_at and req.a.cut_at < req.a.duration - 0.2:
        out.append({"ok": True, "text": f"A será cortado em {req.a.cut_at:.2f}s de {req.a.duration:.2f}s."})
    return out


SYSTEM = (
    "You are an editor and director of photography for premium TV commercials. You plan the transition "
    "between shot A and shot B. Reply JSON with Portuguese (pt-BR) text fields: "
    '{"kind":"one of the allowed kinds","duration":seconds,"why_pt":"1-2 sentences",'
    '"camera_plan_pt":"how the camera moves across the cut, concrete and visual, 2-4 sentences",'
    '"beats":[{"t":"e.g. A -0.5s / corte / B +0.3s","what_pt":"..."}],'
    '"b_camera_start_pt":"how B should open (camera) to match",'
    '"b_prompt_hint_en":"English camera+motion phrase to append to B\'s video prompt so it continues A",'
    '"bridge_prompt_en":"English video-model prompt for a generated bridge clip that STARTS exactly on A\'s exit frame and '
    'ENDS exactly on B\'s entry frame (60-90 words). It must evolve GRADUALLY over the whole clip with ONE physical '
    'camera move that connects both images (push into a glass/reflective surface and emerge in B, rack focus through '
    'foreground, whip pan, crane over an object, shape or color match). No hard cuts, no brand names or text.",'
    '"risks_pt":["..."]}'
)


def plan(req: PlanRequest) -> dict:
    found = checks(req)
    cap_a, cap_b = caption_for(req.a.frame_url), caption_for(req.b.frame_url)
    allowed = {k: v["label"] for k, v in TRANSITIONS.items()}
    ctx = "\n".join(x for x in [
        f"Allowed kinds: {allowed}",
        f"Requested kind: {req.kind} (if 'auto', choose the best).",
        f"Generated bridge duration: {req.duration}s." if req.kind == "bridge" else "",
        f"Director's idea for the camera passage (pt-BR, follow it): {req.idea}" if req.idea else "",
        f"Shot A '{req.a.name}': intent={req.a.intent}; camera={req.a.move}; exit frame={cap_a or 'unknown'}",
        f"Shot B '{req.b.name}': intent={req.b.intent}; camera={req.b.move}; entry frame={cap_b or 'same as A exit frame'}",
        f"Continuity checks: {[c['text'] for c in found]}",
        f"Brand mood: {req.kit.mood}; palette: {req.kit.palette}",
    ] if x)
    data = llm.chat_json(prompts.get("transition"), ctx)
    if data and data.get("camera_plan_pt"):
        source = llm.source()
        if req.kind != "auto":
            data["kind"] = req.kind
        if data.get("kind") not in TRANSITIONS:
            data["kind"] = "dissolve"
    else:
        kind = req.kind if req.kind in TRANSITIONS else ("dissolve" if any(not c["ok"] for c in found) else "cut")
        data = {
            "kind": kind, "duration": TRANSITIONS[kind]["default"],
            "why_pt": "Sem modelo de texto configurado: escolha por regras de continuidade.",
            "camera_plan_pt": f"A termina e B entra com {TRANSITIONS[kind]['label'].lower()}.",
            "beats": [], "b_camera_start_pt": "", "b_prompt_hint_en": "",
            "bridge_prompt_en": f"{req.a.intent}, camera moves continuously towards {req.b.intent}",
            "risks_pt": [],
        }
        source = "regras"
    data["duration"] = float(data.get("duration") or TRANSITIONS[data["kind"]]["default"])
    return {**data, "label": TRANSITIONS[data["kind"]]["label"], "checks": found, "source": source,
            "captions": {"a": cap_a, "b": cap_b}}


def catalog() -> dict:
    return {k: {"label": v["label"], "default": v["default"]} for k, v in TRANSITIONS.items()}
