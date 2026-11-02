"""Relight e transferência de estilo: imagem -> imagem, mantendo composição e identidade.

Dois modos no mesmo node:
- relight: presets de luz (dourada, estúdio, neon, noite, contraluz) + direção da luz (seta desenhada) + intensidade;
- estilo: uma segunda imagem dá paleta, luz, textura e clima; a força diz quanto aplicar.

Caminhos (mesmo padrão do upscale): com modelo de imagem que aceite referências a IA edita a imagem original;
sem ele, um ajuste local com Pillow, e a UI avisa que é simples.
"""
import math
from typing import Literal

from PIL import Image, ImageEnhance, ImageStat
from pydantic import BaseModel, Field, field_validator

from . import direction, registry

LOCAL_ID = "local"

PRESETS = {
    "golden": {"label": "Luz dourada", "text": "golden hour sunlight: warm low sun, long soft shadows, amber highlights",
               "tint": (1.10, 0.98, 0.80), "brightness": 1.0, "saturation": 1.1},
    "studio": {"label": "Estúdio", "text": "clean studio lighting: soft even key and fill light, subtle rim light, neutral background",
               "tint": (1.0, 1.0, 1.0), "brightness": 1.05, "saturation": 1.0},
    "neon": {"label": "Neon", "text": "neon lighting: saturated magenta and cyan light sources with colored reflections on surfaces and a dark ambient",
             "tint": (1.0, 0.85, 1.15), "brightness": 0.95, "saturation": 1.35},
    "night": {"label": "Noite", "text": "night scene: dim moonlight and practical street lights, deep shadows, cool blue tones",
              "tint": (0.78, 0.86, 1.08), "brightness": 0.8, "saturation": 0.9},
    "backlight": {"label": "Contraluz", "text": "strong backlight: the light source is behind the subject, glowing rim-lit edges, the front partly in shadow",
                  "tint": (1.05, 1.0, 0.94), "brightness": 0.9, "saturation": 1.0},
}


class RelightRequest(BaseModel):
    node_id: str = ""
    name: str = "Relight"
    source_url: str
    preset: Literal["golden", "studio", "neon", "night", "backlight"] = "studio"
    intensity: int = Field(60, ge=0, le=100)
    # seta da luz desenhada sobre a imagem, em coordenadas 0..1: origem = de onde a luz vem, ponta = para onde vai
    light: list[float] | None = Field(default=None, min_length=4, max_length=4)
    model: str | int | None = "auto"

    @field_validator("light")
    @classmethod
    def _unit_square(cls, v):
        if v is not None and any(not 0 <= x <= 1 for x in v):
            raise ValueError("coordenadas da luz entre 0 e 1")
        return v


class StyleRequest(BaseModel):
    node_id: str = ""
    name: str = "Estilo"
    source_url: str
    style_url: str  # segunda entrada: imagem de referência de estilo
    strength: int = Field(60, ge=0, le=100)
    prompt: str = Field("", max_length=1000)
    model: str | int | None = "auto"


def pick(user: dict, choice) -> dict | None:
    """Modelo com referências para a passada de IA; None = ajuste local."""
    if choice == LOCAL_ID:
        return None
    return registry.pick(user, "image", choice, {"refs": True})


def local_model(label: str) -> dict:
    return {"id": LOCAL_ID, "kind": "image", "label": label, "provider": "local", "model_id": "pillow",
            "caps": {}, "fields": {}, "params": {}, "cost": 0.0, "cost_unit": "run", "key_id": None,
            "key_name": "", "is_default": 0, "enabled": 1}


def _words(v: int) -> str:
    return "subtle" if v < 35 else "clear" if v < 70 else "strong"


def light_text(light: list[float] | None) -> str:
    if not light:
        return ""
    x0, y0, x1, y1 = light
    return (f"The light comes from the {direction._where(x0, y0)} side and travels toward the {direction._where(x1, y1)} "
            "side: shadows fall on the opposite side and highlights appear on the side facing the light.")


def relight_prompt(req: RelightRequest) -> str:
    p = PRESETS[req.preset]
    return (f"Edit image 1 (the ORIGINAL picture) by relighting it with {p['text']}. {light_text(req.light)} "
            f"Lighting strength: {_words(req.intensity)} ({req.intensity}%). Preserve the identity of every person and "
            "face exactly, and keep all objects, products, logos, composition, framing, perspective and the colors of the "
            "subjects unchanged: only the light, shadows and ambient color change. No drawn marks, arrows or annotations "
            "in the result.")


def style_prompt(req: StyleRequest) -> str:
    note = f" Extra note: {req.prompt.strip()}." if req.prompt.strip() else ""
    return (f"Image 1 is the ORIGINAL photo; image 2 is the STYLE reference. Restyle image 1 so that its color palette, "
            f"lighting, texture and mood match image 2, at {_words(req.strength)} strength ({req.strength}%).{note} "
            "Preserve the identity of every person and face, and keep the objects, products, logos, composition and framing "
            "of image 1 unchanged. No drawn marks in the result.")


# ---------- caminho local (Pillow, sem provedor) ----------

def _gradient_mask(size: tuple[int, int], light: list[float]) -> Image.Image:
    """Máscara L: clara do lado de onde a luz vem, escura do lado oposto (gradiente girado)."""
    w, h = size
    x0, y0, x1, y1 = light
    dx, dy = x0 - x1, y0 - y1  # aponta para a origem da luz
    mask = Image.linear_gradient("L").resize((w, h))  # escuro em cima, claro embaixo
    angle = math.degrees(math.atan2(dx, dy))
    return mask.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=128)  # ponta clara aponta para a luz


def local_relight(src: Image.Image, preset: str, intensity: int, light: list[float] | None) -> Image.Image:
    p = PRESETS[preset]
    base = src.convert("RGB")
    r, g, b = base.split()
    tr, tg, tb = p["tint"]
    graded = Image.merge("RGB", (r.point(lambda v: min(255, int(v * tr))),
                                 g.point(lambda v: min(255, int(v * tg))),
                                 b.point(lambda v: min(255, int(v * tb)))))
    graded = ImageEnhance.Color(graded).enhance(p["saturation"])
    graded = ImageEnhance.Brightness(graded).enhance(p["brightness"])
    if light:
        mask = _gradient_mask(base.size, light)
        lit = ImageEnhance.Brightness(graded).enhance(1.12)
        shade = ImageEnhance.Brightness(graded).enhance(0.72)
        graded = Image.composite(lit, shade, mask)
    return Image.blend(base, graded, max(0.0, min(1.0, intensity / 100)))


def local_style(src: Image.Image, ref: Image.Image, strength: int) -> Image.Image:
    """Transferência de cor por estatística (média e desvio por canal) da referência, misturada pela força."""
    base = src.convert("RGB")
    small = ref.convert("RGB")
    small.thumbnail((256, 256))
    ss, sr = ImageStat.Stat(base), ImageStat.Stat(small)
    lut = []
    for band in range(3):
        ms, ds = ss.mean[band], max(ss.stddev[band], 1e-6)
        mr, dr = sr.mean[band], sr.stddev[band]
        lut += [max(0, min(255, round((x - ms) * (dr / ds) + mr))) for x in range(256)]
    matched = base.point(lut)
    return Image.blend(base, matched, max(0.0, min(1.0, strength / 100)))


def apply_local(op: dict, src_path, dest, ref_path=None) -> None:
    """Executa o ajuste local conforme a operação do job e grava em `dest` (PNG)."""
    with Image.open(src_path) as im:
        src = im.copy()
    if op["kind"] == "relight":
        out = local_relight(src, op["preset"], op["intensity"], op.get("light"))
    elif op["kind"] == "style":
        with Image.open(ref_path) as ref:
            out = local_style(src, ref.copy(), op["strength"])
    else:
        raise ValueError("operação local desconhecida")
    out.save(dest, format="PNG")
