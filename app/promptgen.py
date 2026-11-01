"""Motor de prompts automáticos ("por trás").

O usuário escreve uma intenção curta em português e desenha o caminho de câmera.
Antes de ir para a GPU, este módulo monta o prompt técnico em inglês:

  intenção (pt-BR) + legenda da imagem inicial (visão) + análise do caminho de câmera
  + brand kit + continuidade com o plano anterior  ->  modelo de texto do painel  ->  prompt técnico

O prompt final fica salvo no job (transparência/auditoria) e pode ser editado.
"""
import math
from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import llm, prompts, storage
from .brand import BrandKit, brand_guard


class PathPoint(BaseModel):
    """Keyframe do rig. Posição/aproximação no quadro + rotações + lente."""
    x: float  # centro do enquadramento, 0..1 (esquerda→direita)
    y: float  # 0..1 (topo→base)
    z: float = 1.0  # aproximação física (dolly): 1 = plano aberto, 2 = 2x mais perto
    r: float = 0.0  # roll em graus
    yaw: float = 0.0  # pan (girar a câmera): + = direita, graus
    pitch: float = 0.0  # tilt: + = para cima, graus
    lens: float | None = None  # distância focal em mm (zoom ótico)
    t: float | None = None  # tempo normalizado 0..1 (None = espaçado igualmente)
    ease: str | None = None  # curva do segmento que começa aqui: ease | in | out | linear | hold


class CameraPath(BaseModel):
    points: list[PathPoint] = Field(default_factory=list)
    easing: str = "ease"  # padrão para keyframes sem curva própria
    target: list[float] | None = None
    shake: float = 0.0  # tremido de câmera na mão, 0..1
    duration: float | None = None  # duração do plano (s), para descrever os tempos


EASE_TXT = {"ease": "smooth ease-in-out", "in": "accelerating", "out": "decelerating to a soft stop",
            "linear": "constant speed", "hold": "holding still"}


def _framing(z: float, lens: float) -> str:
    eff = z * lens / 35
    return ("extreme wide shot" if eff < 0.8 else "wide shot" if eff < 1.25 else "medium shot" if eff < 1.9
            else "medium close-up" if eff < 2.8 else "close-up" if eff < 4.2 else "extreme close-up")


def _phrase_segment(a: PathPoint, b: PathPoint) -> str | None:
    la, lb = a.lens or 35, b.lens or 35
    dx, dy, dz, dr = b.x - a.x, b.y - a.y, b.z / max(a.z, 0.01), b.r - a.r
    dyaw, dpitch, dl = b.yaw - a.yaw, b.pitch - a.pitch, lb / la
    parts = []
    if dz > 1.12 and dl < 0.85:
        parts.append(f"performs a dolly zoom (moves in while the lens widens from {la:.0f}mm to {lb:.0f}mm)")
    elif dz < 0.89 and dl > 1.15:
        parts.append(f"performs a reverse dolly zoom (pulls back while zooming from {la:.0f}mm to {lb:.0f}mm)")
    else:
        if dz > 1.12:
            parts.append(f"dollies in ({a.z:.1f}x to {b.z:.1f}x closer)")
        elif dz < 0.89:
            parts.append(f"dollies out ({a.z:.1f}x to {b.z:.1f}x)")
        if dl > 1.15:
            parts.append(f"zooms in optically from {la:.0f}mm to {lb:.0f}mm")
        elif dl < 0.87:
            parts.append(f"zooms out optically from {la:.0f}mm to {lb:.0f}mm")
    if abs(dx) > 0.06:
        parts.append(f"tracks {'right' if dx > 0 else 'left'}")
    if abs(dy) > 0.06:
        parts.append("cranes down" if dy > 0 else "cranes up")
    if abs(dyaw) > 4:
        parts.append(f"pans {'right' if dyaw > 0 else 'left'} {abs(dyaw):.0f} degrees")
    if abs(dpitch) > 4:
        parts.append(f"tilts {'up' if dpitch > 0 else 'down'} {abs(dpitch):.0f} degrees")
    if abs(dr) > 4:
        parts.append(f"rolls {'clockwise' if dr > 0 else 'counter-clockwise'} {abs(dr):.0f} degrees")
    return " and ".join(parts) if parts else None


def _times(pts: list[PathPoint]) -> list[float]:
    n = len(pts)
    raw = [p.t for p in pts]
    if all(t is not None for t in raw):
        return [max(0.0, min(1.0, float(t))) for t in raw]
    return [i / max(1, n - 1) for i in range(n)]


def _classify(a: PathPoint, b: PathPoint, turn: float, span: float) -> str:
    la, lb = a.lens or 35, b.lens or 35
    dx, dy = b.x - a.x, b.y - a.y
    dz = math.log(max(b.z, .01) / max(a.z, .01))
    dl = math.log(lb / la)
    dyaw, dpitch, dr = b.yaw - a.yaw, b.pitch - a.pitch, b.r - a.r
    if dz > 0.1 and dl < -0.15:
        return "dolly_zoom"
    if turn > 1.2 and abs(dx) > 0.1 or (abs(dx) > 0.12 and abs(dyaw) > 8 and dx * dyaw < 0):
        return "orbit"
    if abs(dyaw) > 25 and span < 0.3:
        return "whip_pan"
    if dl > 0.6 and span < 0.35:
        return "crash_zoom"
    scores = {"dolly": abs(dz) * 1.4, "zoom": abs(dl) * 1.2, "truck": abs(dx), "crane": abs(dy),
              "pan": abs(dyaw) / 30, "tilt": abs(dpitch) / 25, "roll": abs(dr) / 45}
    dom = max(scores, key=scores.get)
    if scores[dom] < 0.05:
        return "static"
    return {
        "dolly": ("push_in" if dz < 0.25 else "dolly_in") if dz > 0 else "dolly_out",
        "zoom": "zoom_in" if dl > 0 else "zoom_out",
        "truck": "truck_right" if dx > 0 else "truck_left",
        "crane": "crane_down" if dy > 0 else "crane_up",
        "pan": "pan_right" if dyaw > 0 else "pan_left",
        "tilt": "tilt_up" if dpitch > 0 else "tilt_down",
        "roll": "roll",
    }[dom]


def analyze_path(path: CameraPath) -> dict:
    """Keyframes desenhados -> movimento dominante + descrição cinematográfica com tempos."""
    pts = path.points
    if not pts:
        return {"move": "static", "description": "locked-off static camera", "segments": []}
    times = _times(pts)
    order = sorted(range(len(pts)), key=lambda i: times[i])
    pts, times = [pts[i] for i in order], [times[i] for i in order]
    a, b = pts[0], pts[-1]
    start = (f"camera starts on a {_framing(a.z, a.lens or 35)} ({a.lens or 35:.0f}mm lens) framing the "
             f"{'top' if a.y < .4 else 'bottom' if a.y > .6 else 'middle'}-"
             f"{'left' if a.x < .4 else 'right' if a.x > .6 else 'center'} of the scene")
    if a.pitch > 6:
        start += ", low angle looking up"
    elif a.pitch < -6:
        start += ", high angle looking down"
    if abs(a.r) > 4:
        start += f", dutch angle {a.r:.0f} degrees"
    shake = max(0.0, min(1.0, path.shake))
    if len(pts) < 2:
        move = "handheld" if shake > 0.3 else "static"
        tail = "handheld with natural shake" if shake > 0.3 else "the camera stays locked-off"
        return {"move": move, "description": f"{start}; {tail}", "segments": []}

    turn = 0.0
    for p, q, r in zip(pts, pts[1:], pts[2:]):
        v1, v2 = (q.x - p.x, q.y - p.y), (r.x - q.x, r.y - q.y)
        if math.hypot(*v1) > 1e-3 and math.hypot(*v2) > 1e-3:
            turn += abs(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]))
    span = times[-1] - times[0]
    move = _classify(a, b, turn, span)
    if move == "static" and shake > 0.3:
        move = "handheld"

    dur = path.duration
    raw = []  # (início, fim, frase, curva); trechos seguidos iguais viram um só
    for (p, tp), (q, tq) in zip(zip(pts, times), zip(pts[1:], times[1:])):
        ease = p.ease or path.easing or "ease"
        phrase = "holds the framing" if ease == "hold" else _phrase_segment(p, q)
        if not phrase:
            if tq - tp > 0.15:
                phrase = "holds the framing"
            else:
                continue
        core = phrase.split(" (")[0]
        if raw and raw[-1][2].split(" (")[0] == core and raw[-1][3] == ease:
            raw[-1] = (raw[-1][0], tq, raw[-1][2], ease)
        else:
            raw.append((tp, tq, phrase, ease))
    segments = []
    for tp, tq, phrase, ease in raw:
        when = f"from {tp * dur:.1f}s to {tq * dur:.1f}s " if dur else ""
        speed = "" if ease == "hold" else f", {EASE_TXT.get(ease, EASE_TXT['ease'])}"
        segments.append(f"{when}the camera {phrase}{speed}")
    if times[0] > 0.05:
        segments.insert(0, f"holds still until {times[0] * dur:.1f}s" if dur else "holds still at first")
    body = "; then ".join(segments) or "camera holds steady"
    if move == "orbit":
        body += "; overall an orbiting arc around the subject"
    if shake > 0.15:
        body += f"; {'strong' if shake > 0.6 else 'subtle'} handheld shake throughout"
    return {"move": move, "description": f"{start}; {body}", "segments": segments, "turn": round(turn, 2)}


_captions: dict[str, str] = {}


def caption_for(image_url: str | None) -> str | None:
    if not image_url:
        return None
    if image_url not in _captions:
        try:
            path = storage.resolve(image_url)
        except HTTPException:
            return None
        cap = llm.describe_image(path)
        if not cap:
            return None
        _captions[image_url] = cap.strip()
    return _captions[image_url]


SYSTEM = (
    "You are the prompt engineer of a premium commercial production studio working FOR the brands it "
    "advertises. You write prompts for a state-of-the-art video model and for a still-image model. Rules: English only; "
    "60-110 words for video_prompt; structure = subject + action/motion + environment + lighting + camera "
    "movement + lens + style; describe ONE continuous shot, no cuts; for image-to-video, stay faithful to the "
    "start-frame description and focus on what moves; keep the exact camera movement given. "
    "BRANDS (critical, this is client work): every brand, product name, logo or slogan the director mentions "
    "MUST appear in keyframe_prompt with the EXACT spelling, in quotes for printed text (e.g. label reading "
    "'Acme'). Never drop, translate, genericize or misspell a brand. keyframe_prompt goes to an image model "
    "that renders text well and may receive reference images numbered image 1..N: refer to them explicitly "
    "(e.g. 'the logo from image 1 printed on the label', 'the bottle exactly as in image 2'). References give the "
    "PRODUCT, LOGO or STYLE only: always add 'ignore the background of image N' and describe the NEW scene the "
    "director asked for in full, unless the director explicitly wants the reference's scene. video_prompt goes "
    "to the video model, which cannot reliably write text: name the branded product ('the Acme bottle') and say its label and "
    "logo stay sharp and unchanged; never ask the video model to form letters. PRODUCT SHOTS: the product is a rigid, unchanging object "
    "(same shape, same label, no liquid pouring, no opening, no morphing); all motion comes from the camera "
    "and the environment (droplets, mist, particles, light). Avoid verbs that make the product act "
    "(pour, splash out, open, transform). Put 'product deforming, label changing, liquid pouring' in negative_extra. "
    "Reply JSON: "
    '{"title":"pt-BR 3-5 words","video_prompt":"...","keyframe_prompt":"still image prompt, no motion words",'
    '"negative_extra":"comma list","notes_pt":"one short sentence in Portuguese explaining choices"}'
)


ADAPT_SYSTEM = (
    "You fix image-to-video prompts. You get the ACTUAL first frame description and a draft video "
    "prompt written before the frame existed. Rewrite the prompt so it is 100% consistent with the first frame: "
    "same setting, objects, light and composition as the frame; remove anything the frame contradicts (never move "
    "the scene to a different place). Keep: brand/product names exactly, the camera movement, the requested "
    "motion/atmosphere, and the rule that labels/logos stay sharp and unchanged. 60-110 words. "
    'Reply JSON: {"video_prompt":"..."}'
)


def adapt_to_frame(video_prompt: str, frame_url: str) -> tuple[str, str | None]:
    """Depois que o still existe: reescreve o prompt de vídeo para bater com o 1º frame real.

    Evita que o modelo de vídeo "pule" de cenário quando o still não ficou igual ao que o prompt imaginava.
    """
    caption = caption_for(frame_url)
    if not caption:
        return video_prompt, None
    data = llm.chat_json(prompts.get("adapt_frame"), f"First frame (actual): {caption}\nDraft video prompt: {video_prompt}")
    return (data or {}).get("video_prompt") or video_prompt, caption


def _direction_lines(d: dict) -> list[str]:
    """Plano desenhado pelo diretor sobre o 1º frame: vira instrução obrigatória."""
    lines = ["The director DREW the motion on the start frame. Follow it EXACTLY (it is mandatory, do not invent "
             "other camera moves or other moving objects):"]
    if d.get("camera_en"):
        lines.append(f"- Camera: {d['camera_en']}")
    for e in d.get("elements") or []:
        if e.get("action_en"):
            lines.append(f"- {e.get('target_en') or 'element'}: {e['action_en']}")
    if d.get("motion_prompt_en"):
        lines.append(f"- Combined: {d['motion_prompt_en']}")
    lines.append("Never show drawn lines, arrows or circles in the video.")
    return lines


class AutoPromptRequest(BaseModel):
    intent: str
    mode: str = "t2v"  # t2v | i2v | keyframe
    start_image_url: str | None = None
    camera_path: CameraPath | None = None
    lens: str = "35"
    duration: float = 5
    previous_prompt: str | None = None
    transition_hint: str | None = None  # vem do card de transição: como B deve abrir para continuar A
    end_image_url: str | None = None  # ponte A→B: frame de chegada
    ref_image_urls: list[str] = Field(default_factory=list)  # referências (logo, produto) na ordem image 1..N
    feedback: list[str] = Field(default_factory=list)  # comentários de reprovação (nó Aprovação)
    cast: list[dict] = Field(default_factory=list)  # elenco/produto recorrente: {name, kind, desc, images: [n...]}
    direction: dict | None = None  # plano desenhado sobre o 1º frame (direction.interpret)
    camera_hint: str | None = None  # sugestão de câmera do Roteiro (sem frame desenhado)
    kit: BrandKit = Field(default_factory=BrandKit)


def autoprompt(req: AutoPromptRequest) -> dict:
    cam = analyze_path(req.camera_path) if req.camera_path and req.camera_path.points else None
    caption = caption_for(req.start_image_url) if req.mode == "i2v" else None
    k = req.kit
    ctx = [
        f"Director intent (pt-BR): {req.intent}",
        f"Mode: {req.mode}. Duration: {req.duration}s.",
        *(_direction_lines(req.direction) if req.direction else [
            f"Camera movement (MANDATORY): {cam['description']}" if cam else
            f"Camera suggestion from the script: {req.camera_hint}" if req.camera_hint else
            "Camera: choose a fitting commercial move."]),
        f"Lens: {req.lens}mm",
        f"Start frame contents: {caption}" if caption else "",
        f"Previous shot (keep continuity of light/palette): {req.previous_prompt}" if req.previous_prompt else "",
        f"Transition from previous shot (MANDATORY opening camera motion): {req.transition_hint}" if req.transition_hint else "",
        f"The shot must END on this frame: {caption_for(req.end_image_url)}" if req.end_image_url else "",
        f"Brand (write EXACTLY like this): '{k.brand}'" if k.brand and k.brand != "Marca" else "",
        ("Client/director REJECTED the previous render. Revision notes (pt-BR) you MUST address, newest last: "
         + " | ".join(req.feedback)) if req.feedback else "",
        f"Product (visual): {k.product}. Palette: {k.palette}. Mood: {k.mood}.",
        *[f"Reference image {i} (refer to it as 'image {i}'): {caption_for(u) or 'reference provided by the director'}"
          for i, u in enumerate(req.ref_image_urls, 1)],
        *[(f"RECURRING {str(c.get('kind') or 'element').upper()} '{c.get('name')}' (must look IDENTICAL in every shot: same "
           f"face/shape, colors, clothing/label): {c.get('desc') or 'see references'}"
           + (f". Shown in reference image(s) {', '.join(str(x) for x in c.get('images') or [])}" if c.get('images') else ""))
          for c in req.cast if c.get("name")],
    ]
    data = llm.chat_json(prompts.get("autoprompt"), "\n".join(c for c in ctx if c))
    if data and data.get("video_prompt"):
        source = llm.source()
    else:
        cam_txt = cam["description"] if cam else "slow cinematic camera move"
        data = {
            "title": req.intent[:40],
            "video_prompt": f"{req.intent}. {caption or ''} {cam_txt}, {req.lens}mm lens, {k.mood}, "
                            f"{k.palette} color palette, commercial cinematography, volumetric light",
            "keyframe_prompt": f"{req.intent}, {k.palette}, commercial key visual, photorealistic",
            "negative_extra": "",
            "notes_pt": "Sem modelo de texto configurado: prompt montado por regras.",
        }
        source = "regras"
    video, warns = brand_guard(data["video_prompt"], k)
    key, warns2 = brand_guard(data.get("keyframe_prompt", ""), k)
    return {**data, "video_prompt": video, "keyframe_prompt": key, "camera": cam, "caption": caption,
            "source": source, "warnings": warns + warns2}
