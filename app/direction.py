"""Direção desenhada sobre o 1º frame.

O usuário desenha em cima do frame real: em ROXO a rota da câmera; em outras cores, elementos
(circula um objeto e/ou puxa uma linha mostrando para onde ele vai) e escreve num cartão por cor o
que deve acontecer. O modelo de visão recebe o frame JÁ com o desenho + os textos e devolve o
plano de movimento ligado ao conteúdo da imagem ("a bola vermelha circulada em amarelo...").
Sem modelo de visão, o plano é montado com os textos + a geometria dos traços.
"""
import base64
import io
import json

from pydantic import BaseModel, Field

from . import llm, promptgen, prompts

COLOR_NAMES = {"camera": "purple", "yellow": "yellow", "cyan": "cyan", "pink": "pink", "orange": "orange",
               "green": "green", "red": "red", "blue": "blue", "white": "white"}


class Shape(BaseModel):
    type: str  # loop (circulou algo) | path (linha/seta de movimento)
    points: list[list[float]] = Field(default_factory=list)  # [[x, y], ...] normalizados 0..1


class Layer(BaseModel):
    color: str  # camera | yellow | cyan ...
    label: str = ""  # "AMARELO"
    prompt: str = ""  # o que o usuário escreveu no cartão
    shapes: list[Shape] = Field(default_factory=list)


class DirectionRequest(BaseModel):
    annotated: str  # data URL (jpeg/png) do frame com o desenho
    layers: list[Layer] = Field(default_factory=list)
    intent: str = ""
    duration: float = 5


def _where(x: float, y: float) -> str:
    h = "left" if x < .36 else "right" if x > .64 else "center"
    v = "top" if y < .36 else "bottom" if y > .64 else "middle"
    return f"{v}-{h}"


def _geometry(layer: Layer) -> str:
    """Descrição objetiva dos traços (vale mesmo sem modelo de visão)."""
    parts = []
    for s in layer.shapes:
        if len(s.points) < 2:
            continue
        xs, ys = [p[0] for p in s.points], [p[1] for p in s.points]
        if s.type == "loop":
            parts.append(f"circled area at the {_where(sum(xs) / len(xs), sum(ys) / len(ys))} "
                         f"(~{(max(xs) - min(xs)) * 100:.0f}% wide)")
        else:
            a, b = s.points[0], s.points[-1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            direction = " and ".join(d for d in [
                ("right" if dx > 0 else "left") if abs(dx) > .05 else "",
                ("down" if dy > 0 else "up") if abs(dy) > .05 else ""] if d) or "in place"
            span = f"{(dx * dx + dy * dy) ** .5 * 100:.0f}% of the frame"
            if layer.color == "camera":  # rota da câmera: ponto = começo, seta = fim
                parts.append(f"camera route from the {_where(*a)} (dot = start) to the {_where(*b)} (arrow = end) "
                             f"(moving {direction}, {span})")
            else:  # elemento: ponto cheio = onde começa, anel vazio = onde termina
                parts.append(f"element path from the {_where(*a)} (solid dot = start) to the {_where(*b)} "
                             f"(hollow ring = end) (moving {direction}, {span})")
    return "; ".join(parts)


def _camera_path(layer: Layer) -> dict | None:
    path = next((s for s in layer.shapes if s.type == "path" and len(s.points) >= 2), None)
    if not path:
        return None
    raw = path.points
    idx = sorted({0, len(raw) // 3, (2 * len(raw)) // 3, len(raw) - 1})  # traço à mão: começo, meio, fim
    pts = [promptgen.PathPoint(x=raw[i][0], y=raw[i][1], z=1.0) for i in idx]
    return promptgen.analyze_path(promptgen.CameraPath(points=pts))


SYSTEM = (
    "You are a director of photography. You receive the FIRST FRAME of a shot with the director's drawing on top "
    "of it, plus a note per color. PURPLE is the CAMERA: its line is the route of the camera over the image "
    "(dot = start, arrow = end; going toward something means moving the camera toward it). Every OTHER color is "
    "an ELEMENT: a closed loop circles the object that must move; an open line starts at a solid DOT (where the "
    "element starts) and ends at a hollow RING (where it goes). "
    "Look at the image to identify exactly WHAT is circled or touched by each color. The drawing itself is NOT part "
    "of the scene (never put lines, arrows or circles in the video). Reply JSON: "
    '{"camera_en":"precise English camera instruction tied to the image content (start framing, route, end '
    'framing, speed)","camera_pt":"o mesmo em português, curto",'
    '"elements":[{"color":"yellow","target_en":"what is circled, e.g. the red ball on the table",'
    '"action_en":"what it does, e.g. rolls slowly to the right","action_pt":"em português"}],'
    '"motion_prompt_en":"one paragraph combining camera + all element motions, for a video model",'
    '"warnings_pt":["contradições ou coisas impossíveis, se houver"]}'
)


def interpret(req: DirectionRequest) -> dict:
    cam = next((l for l in req.layers if l.color == "camera"), None)
    elems = [l for l in req.layers if l.color != "camera" and (l.shapes or l.prompt.strip())]
    geo_cam = _camera_path(cam) if cam else None
    notes = [
        f"Shot intent (pt-BR): {req.intent}" if req.intent else "",
        f"Shot duration: {req.duration}s.",
        (f"PURPLE (camera) note: {cam.prompt or '(no note)'}; drawing: {_geometry(cam) or 'no line drawn'}") if cam else
        "No camera drawing: keep the camera nearly static unless the notes ask otherwise.",
        *[f"{COLOR_NAMES.get(l.color, l.color).upper()} ({l.label}) note: {l.prompt or '(no note)'}; drawing: "
          f"{_geometry(l) or 'nothing drawn'}" for l in elems],
    ]
    raw = llm.chat([{"role": "system", "content": prompts.get("direction")}, {"role": "user", "content": [
        {"type": "text", "text": "\n".join(n for n in notes if n)},
        {"type": "image_url", "image_url": {"url": _shrink(req.annotated)}},
    ]}], kind="vision", as_json=True, timeout=90)
    data = None
    if raw:
        try:
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        except ValueError:
            data = None
    if data and data.get("motion_prompt_en"):
        data["source"] = llm.source()
    else:  # sem visão: textos do usuário + geometria
        cam_txt = ". Drawn route: ".join(x for x in [cam.prompt if cam else "", geo_cam["description"] if geo_cam else ""] if x)
        el = [{"color": l.color, "target_en": _geometry(l), "action_en": l.prompt, "action_pt": l.prompt} for l in elems]
        data = {"camera_en": cam_txt or "camera nearly static", "camera_pt": (cam.prompt if cam else "") or "câmera quase parada",
                "elements": el,
                "motion_prompt_en": "",  # as linhas de câmera/elementos já vão separadas para o prompt
                "warnings_pt": ["Sem modelo de visão configurado: a IA não olhou a imagem, só os textos e a posição dos traços."],
                "source": "regras"}
    data["camera_move"] = geo_cam["move"] if geo_cam else "static"
    return data


def _shrink(data_url: str, max_side: int = 1280) -> str:
    """Garante uma imagem leve para o modelo de visão."""
    try:
        from PIL import Image
        head, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        if len(raw) < 900_000:
            return data_url
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return data_url
