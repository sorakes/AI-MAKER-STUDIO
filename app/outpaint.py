"""Expandir (outpaint) e editar região (inpaint) por máscara.

Regra de segurança do resultado: a IA recebe a imagem com a área a mudar marcada (cinza no outpaint, magenta
no inpaint). Ao final, o servidor recompõe o original sobre tudo que a máscara protege, então nenhum traço
de marcação aparece no resultado e o resto da imagem fica idêntico ao original, mesmo que a IA erre.

Sem modelo com referências: outpaint por preenchimento com desfoque (Pillow) e inpaint por preenchimento
suave da região; a UI avisa que é simples.
"""
from pathlib import Path
from typing import Literal

import json
import re

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
    reinforce: bool = False  # nova tentativa: a IA não mexeu na região na primeira vez
    # clean: original limpa como entrada (padrão); clean_mask: original + máscara como 2ª referência;
    # marked: original com a região em magenta (como antes). Só vale com a identificação; sem ela, usa marked.
    input_mode: Literal["clean", "clean_mask", "marked"] = "clean"


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


REMOVE_RE = re.compile(
    r"\b(remov\w*|tir\w*|apag\w*|elimin\w*|delet\w*|quit\w*|retir\w*|suma\w*|sumir|sem\s+(o|a|os|as))\b"
    r"|circul\w*|pintad\w*|marcad\w*|\boq\b|o\s+que\s+est\w*", re.I)
UNCHANGED_MEAN = 4.0  # diferença média (0-255) dentro da máscara abaixo disso = a IA não mexeu na região


def is_removal(text: str) -> bool:
    """Pedido de remoção (ou sem texto, ou que aponta para a marcação): o modelo tem de apagar e preencher o fundo."""
    t = (text or "").strip()
    return not t or bool(REMOVE_RE.search(t))


def _phrase(value, limit: int = 80) -> str:
    """Texto curto vindo da identificação: uma linha, sem aspas, cortado."""
    s = " ".join(str(value or "").split()).strip(" \"'")
    return s[:limit]


def inpaint_prompt(req: InpaintRequest, reinforce: bool = False, ident: dict | None = None) -> str:
    """Instrução do inpaint.

    Com a identificação (o que está dentro do contorno), o pedido é semântico, em linguagem comum, sem
    magenta nem 'circulado': 'Remove <objeto> from this photo...'. Sem ela (visão falhou), usa a imagem marcada
    e deixa a região magenta como única área a mudar."""
    text = req.prompt.strip()
    removal = is_removal(text)
    target = _phrase((ident or {}).get("target"))
    if target:
        surr = _phrase((ident or {}).get("surroundings")) or "the surrounding background"
        if req.input_mode == "marked":
            if removal:
                body = (f"Image 1 has a solid MAGENTA region over {target}. Remove {target} completely and fill the "
                        f"magenta region with {surr}, plausible and seamless.")
            else:
                body = f"Image 1 has a solid MAGENTA region over {target}. Replace the content of the magenta region with: {text}."
            body += " Everything outside the magenta region must stay pixel-identical to image 1."
        else:
            if removal:
                body = (f"Remove {target} from this photo. Fill the empty area with {surr}, matching lighting, "
                        "perspective and style. Keep everything else in the photo exactly the same.")
            else:
                body = (f"Replace {target} with: {text}. Keep everything else in the photo exactly the same, "
                        "matching lighting and perspective.")
            if req.input_mode == "clean_mask":
                body = "Image 2 is a black-and-white mask: the white area is the only area to change. " + body
        if reinforce:
            body += (f" The previous attempt left {target} visible: it must be completely gone."
                     if removal else " The previous attempt barely changed the area: it must visibly change.")
        return f"{body} Do not add text, borders or marks."
    base = ("Image 1 has a solid MAGENTA region marking the ONLY area to change. "
            "Everything outside the magenta region must stay pixel-identical to image 1. ")
    if removal:
        what = ("Remove the object or part inside the magenta region completely, and fill that area with the "
                "surrounding background, plausible and seamless")
    else:
        what = f"Replace the content inside the magenta region with: {text}"
    extra = (" The previous attempt barely changed the marked area: this time the magenta region MUST visibly change."
             if reinforce else "")
    return f"{base}{what}. Blend lighting, perspective and style with the surroundings.{extra} The result must contain NO magenta and no marks."


IDENT_PROMPT = ('The photo has an area outlined in magenta. Identify what is inside the magenta outline. Answer with JSON '
                'only, values in English: {"target": short noun phrase for what is inside (e.g. "the bee\'s wings"), '
                '"type": "object" | "person" | "text" | "background", "surroundings": short phrase for what should fill '
                'that space once it is removed (e.g. "the bee\'s body and a blurred green background")}.')


def _png_url(img: Image.Image) -> str:
    import base64
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def outline_image(src: Image.Image, mask: Image.Image) -> Image.Image:
    """Original com só o CONTORNO da região em magenta (a visão enxerga o que está dentro)."""
    from PIL import ImageChops
    base = src.convert("RGB")
    m = mask.convert("L").resize(base.size).point(lambda v: 255 if v > 127 else 0)
    r = max(2, max(base.size) // 250)
    inner = m.filter(ImageFilter.GaussianBlur(r)).point(lambda v: 255 if v >= 250 else 0)
    edge = ImageChops.subtract(m, inner)
    return Image.composite(Image.new("RGB", base.size, MARK), base, edge)


def identify_region(src: Image.Image, mask: Image.Image) -> tuple[dict | None, float]:
    """Visão barata: o que está dentro do contorno e o que deve preencher o lugar. Devolve (dados, custo US$).

    Qualquer falha (sem modelo de visão, resposta fora do formato) devolve (None, custo): o pedido segue com o
    prompt de fallback. A identificação nunca derruba o job."""
    from . import llm
    small = src.convert("RGB").copy()
    small.thumbnail((1024, 1024))
    mk = mask.convert("L").resize(small.size)
    try:
        raw, cost = llm.chat_priced([{"role": "user", "content": [
            {"type": "text", "text": IDENT_PROMPT},
            {"type": "image_url", "image_url": {"url": _png_url(outline_image(small, mk))}}]}],
            kind="vision", as_json=True, timeout=60)
    except Exception:  # noqa: BLE001 - visão opcional: qualquer erro cai no fallback
        return None, 0.0
    if not raw:
        return None, float(cost or 0.0)
    try:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        return None, float(cost or 0.0)
    if not isinstance(data, dict) or not _phrase(data.get("target")):
        return None, float(cost or 0.0)
    return {"target": _phrase(data.get("target")), "type": _phrase(data.get("type"), 20) or "object",
            "surroundings": _phrase(data.get("surroundings"))}, float(cost or 0.0)


def inpaint_report(orig_path, raw_path, mask_path, prompt: str, model: str | None) -> dict:
    """Diagnóstico guardado no job: pixels da máscara, mudança dentro/fora, prompt final e modelo."""
    with Image.open(orig_path) as o, Image.open(raw_path) as r, Image.open(mask_path) as m:
        stats = change_stats(o.convert("RGB"), r.convert("RGB"), m.convert("L"))
    return {**stats, "prompt": prompt, "model": model}


def change_stats(original: Image.Image, output: Image.Image, mask: Image.Image) -> dict:
    """Quanto a IA mudou DENTRO da máscara e quanto mudou FORA (o fora deve ser zero por causa da recomposição)."""
    from PIL import ImageChops, ImageStat
    o = output.convert("RGB").resize(original.size)
    diff = ImageChops.difference(original.convert("RGB"), o).convert("L")
    m = mask.convert("L").resize(original.size).point(lambda v: 255 if v > 127 else 0)
    inv = m.point(lambda v: 0 if v else 255)
    inside = ImageStat.Stat(diff, mask=m).mean[0] if m.getbbox() else 0.0
    outside = ImageStat.Stat(diff, mask=inv).mean[0]
    px = sum(1 for v in m.getdata() if v)
    return {"mask_pixels": px, "inside_mean": round(inside, 2), "outside_mean": round(outside, 2),
            "unchanged": bool(px) and inside < UNCHANGED_MEAN}


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


def _match_color(ai: Image.Image, orig: Image.Image, band: Image.Image) -> Image.Image:
    """Ajusta a cor da região gerada à média do entorno (faixa logo fora da máscara), limitado a ±30."""
    from PIL import ImageStat
    so = ImageStat.Stat(orig, mask=band).mean
    sa = ImageStat.Stat(ai, mask=band).mean
    shift = [max(-30, min(30, round(o - a))) for o, a in zip(so, sa)]
    return Image.merge("RGB", [ch.point(lambda v, s=s: max(0, min(255, v + s))) for ch, s in zip(ai.split(), shift)])


def finish_inpaint(ai_path: Path, original_path: Path, mask_path: Path, size: tuple[int, int], dest: Path) -> Path:
    """Recomposição do inpaint com borda suave: a região da IA é a máscara dilatada e esfumada (sem serrilhado
    nem retângulo), com a cor ajustada ao entorno. Fora da máscara dilatada o resultado é o original, pixel a pixel.

    A saída da IA é levada ao tamanho do quadro antes de compor (a IA pode devolver outra dimensão)."""
    from PIL import ImageChops
    with Image.open(ai_path) as a:
        ai = a.convert("RGB")
        if ai.size != size:
            ai = ai.resize(size, Image.Resampling.LANCZOS)
    with Image.open(original_path) as o:
        orig = o.convert("RGB")
        if orig.size != size:
            orig = orig.resize(size, Image.Resampling.LANCZOS)
    with Image.open(mask_path) as mk:
        m = mk.convert("L").resize(size).point(lambda v: 255 if v > 127 else 0)
    sigma = min(12.0, max(6.0, max(size) / 420))  # esfumado: 6 a 12 px conforme a resolução
    dil = m.filter(ImageFilter.GaussianBlur(2 * sigma)).point(lambda v: 255 if v >= 1 else 0)
    if m.getbbox():
        band = ImageChops.subtract(dil, m)  # faixa logo fora da máscara: onde a IA e o original se encontram
        if band.getbbox():
            ai = _match_color(ai, orig, band)
    alpha = ImageChops.multiply(m.filter(ImageFilter.GaussianBlur(sigma)), dil)  # 0 fora da dilatação
    out_path = dest.with_suffix(".png")
    Image.composite(ai, orig, alpha).save(out_path, format="PNG")
    return out_path


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


def finish_ai(ai_path: Path, original_path: Path, keep_path: Path, size: tuple[int, int], dest: Path) -> Path:
    """Leva a saída da IA ao tamanho do quadro e recompõe o original sobre o que a máscara protege.

    Grava em PNG (sem perda): um JPEG recomprimido mudaria os pixels do original. Devolve o caminho gravado."""
    with Image.open(ai_path) as a:
        ai = a.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    with Image.open(original_path) as o:
        orig = o.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    with Image.open(keep_path) as k:
        keep = k.convert("L").resize(size)
    out = dest.with_suffix(".png")
    Image.composite(orig, ai, keep).save(out, format="PNG")
    return out


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
