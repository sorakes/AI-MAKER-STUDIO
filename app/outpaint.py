"""Expandir (outpaint) e editar região (inpaint) por máscara.

Regra de segurança do resultado: a IA recebe a imagem com a área a mudar marcada (cinza no outpaint, magenta
no inpaint). Ao final, o servidor recompõe o original sobre tudo que a máscara protege, então nenhum traço
de marcação aparece no resultado e o resto da imagem fica idêntico ao original, mesmo que a IA erre.

Sem modelo com referências: outpaint por preenchimento com desfoque (Pillow) e inpaint por preenchimento
suave da região; a UI avisa que é simples.
"""
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel, Field

from . import registry

ASPECTS = {"1:1": (1, 1), "16:9": (16, 9), "9:16": (9, 16), "4:5": (4, 5), "21:9": (21, 9)}
ANCHORS = ("center", "top", "bottom", "left", "right")
FILL = (128, 128, 128)   # cinza: área nova do outpaint, marcada para a IA preencher
MARK = (255, 0, 255)     # magenta: região a editar no inpaint, marcada para a IA trocar


class OutpaintRequest(BaseModel):
    node_id: str = ""
    name: str = "Expandir"
    source_url: str
    aspect: Literal["1:1", "16:9", "9:16", "4:5", "21:9"] = "16:9"
    anchor: Literal["center", "top", "bottom", "left", "right"] = "center"  # onde a original fica no quadro
    prompt: str = Field("", max_length=1000)
    model: str | int | None = "auto"


class InpaintRequest(BaseModel):
    node_id: str = ""
    name: str = "Editar região"
    source_url: str
    mask: str  # data URL PNG da máscara pintada (branco = região a editar), do tamanho da imagem
    prompt: str = Field("", max_length=1000)
    model: str | int | None = "auto"


def pick(user: dict, choice) -> dict | None:
    """Modelo com referências para a passada de IA; None = caminho simples local."""
    if choice == "local":
        return None
    return registry.pick(user, "image", choice, {"refs": True})


def layout(w: int, h: int, aspect: str, anchor: str) -> tuple[int, int, int, int]:
    """Quadro final (W, H) no aspecto pedido, contendo a original (w, h) e a posição (x, y) dela."""
    tw, th = ASPECTS[aspect]
    W = max(w, round(h * tw / th))
    H = round(W * th / tw)
    if H < h:
        H = h
        W = round(H * tw / th)
    W = max(W, w)
    if anchor == "center":
        x, y = (W - w) // 2, (H - h) // 2
    elif anchor == "left":
        x, y = 0, (H - h) // 2
    elif anchor == "right":
        x, y = W - w, (H - h) // 2
    elif anchor == "top":
        x, y = (W - w) // 2, 0
    else:  # bottom
        x, y = (W - w) // 2, H - h
    return W, H, x, y


def outpaint_prompt(req: OutpaintRequest, W: int, H: int, x: int, y: int) -> str:
    where = {"center": "centre", "left": "left side", "right": "right side", "top": "top", "bottom": "bottom"}[req.anchor]
    note = f" Extra note: {req.prompt.strip()}." if req.prompt.strip() else ""
    return (f"Image 1 is a {W}x{H} frame; the original picture sits at the {where} of it, and the GREY area around it is "
            f"empty space. Fill the entire grey area by continuing the scene of the original naturally, with the same "
            f"style, lighting, colors and perspective.{note} The original region must stay identical to image 1. The "
            "result must contain NO grey, no borders, no frames and no marks.")


def inpaint_prompt(req: InpaintRequest) -> str:
    what = req.prompt.strip() or "a natural continuation of the surroundings"
    return (f"Image 1 has a solid MAGENTA region marking where to edit. Replace ONLY that magenta region with: {what}. "
            "Blend its lighting, perspective and style with the surroundings. Everything outside the magenta region must "
            "stay identical to image 1. The result must contain NO magenta and no marks.")


def canvas_image(src: Image.Image, W: int, H: int, x: int, y: int) -> Image.Image:
    """Quadro com a original no lugar e o resto em cinza (entrada da IA no outpaint)."""
    out = Image.new("RGB", (W, H), FILL)
    out.paste(src.convert("RGB"), (x, y))
    return out


def keep_for_outpaint(W: int, H: int, x: int, y: int, w: int, h: int) -> Image.Image:
    """Máscara de recomposição: branco sobre a original (o original fica), preto sobre a área nova."""
    m = Image.new("L", (W, H), 0)
    m.paste(255, (x, y, x + w, y + h))
    return m


def marked_image(src: Image.Image, mask: Image.Image) -> Image.Image:
    """Original com a região pintada em magenta sólido (entrada da IA no inpaint)."""
    base = src.convert("RGB")
    return Image.composite(Image.new("RGB", base.size, MARK), base, mask.convert("L"))


def keep_for_inpaint(mask: Image.Image) -> Image.Image:
    """Recomposição do inpaint: branco fora da região pintada (o original fica), preto dentro."""
    return ImageOps.invert(mask.convert("L"))


def local_outpaint(src: Image.Image, W: int, H: int, x: int, y: int) -> Image.Image:
    """Preenchimento simples: a imagem esticada até cobrir o quadro, bem desfocada, e a original por cima."""
    bg = ImageOps.fit(src.convert("RGB"), (W, H)).filter(ImageFilter.GaussianBlur(24))
    bg.paste(src.convert("RGB"), (x, y))
    return bg


def local_inpaint(src: Image.Image, mask: Image.Image) -> Image.Image:
    """Região pintada preenchida com a cor média ao redor, desfocada; o resto fica igual."""
    base = src.convert("RGB")
    m = mask.convert("L").resize(base.size)
    avg = ImageOps.fit(base, (1, 1)).getpixel((0, 0))
    filled = Image.composite(Image.new("RGB", base.size, avg), base, m)
    fill = filled.filter(ImageFilter.GaussianBlur(18))
    return Image.composite(fill, base, m)


def finish_ai(ai_path: Path, original_path: Path, keep_path: Path, size: tuple[int, int], dest: Path) -> None:
    """Leva a saída da IA ao tamanho do quadro e recompõe o original sobre o que a máscara protege."""
    with Image.open(ai_path) as a:
        ai = a.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    with Image.open(original_path) as o:
        orig = o.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    with Image.open(keep_path) as k:
        keep = k.convert("L").resize(size)
    Image.composite(orig, ai, keep).save(dest)


def apply_local(op: dict, paths: dict[str, str], dest: Path) -> None:
    """Caminho simples: `paths` traz os arquivos pelo nome sem o sufixo _url (original, mask)."""
    with Image.open(paths["original"]) as im:
        src = im.copy()
    if op["kind"] == "outpaint":
        out = local_outpaint(src, op["W"], op["H"], op["x"], op["y"])
    elif op["kind"] == "inpaint":
        with Image.open(paths["mask"]) as mk:
            out = local_inpaint(src, mk.copy())
    else:
        raise ValueError("operação local desconhecida")
    out.save(dest, format="PNG")
