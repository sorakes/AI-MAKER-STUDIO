"""Posições (8 direções) de personagem/produto, sugestão de prompts e montagem de spritesheet."""
import json
import math
import uuid

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

import shutil

from . import guides, llm, media, prompts, storage

# Ordem clássica de sprite (sentido horário a partir da frente).
DIRECTIONS = {
    "character": [
        ("front", "Frente", "FRONT VIEW (0 degrees): the character faces the camera directly, face and chest fully visible, "
                            "both shoulders equally visible"),
        ("front_right", "3/4 direita", "THREE-QUARTER FRONT VIEW (45 degrees): the character's body is rotated 45 degrees so the "
                                       "character faces toward the LEFT side of the image; the viewer sees the front and the "
                                       "character's LEFT side; face visible in three-quarter, one eye partially hidden"),
        ("right", "Perfil direito", "SIDE PROFILE (90 degrees): the character faces exactly toward the LEFT edge of the image, "
                                    "pure side view, only the character's LEFT side visible, nose pointing left, one eye visible"),
        ("back_right", "3/4 costas dir.", "THREE-QUARTER BACK VIEW (135 degrees): the character faces away from the camera, "
                                          "turned toward the upper-left; the viewer sees the back and the character's LEFT side; "
                                          "the FACE IS NOT VISIBLE, only a sliver of the cheek and ear"),
        ("back", "Costas", "BACK VIEW (180 degrees): the character faces directly away from the camera; the viewer sees only "
                           "the back of the head, hair, back and heels; the FACE IS NOT VISIBLE AT ALL"),
        ("back_left", "3/4 costas esq.", "THREE-QUARTER BACK VIEW (225 degrees): the character faces away from the camera, "
                                         "turned toward the upper-right; the viewer sees the back and the character's RIGHT side; "
                                         "the FACE IS NOT VISIBLE, the head is NOT turned toward the camera"),
        ("left", "Perfil esquerdo", "SIDE PROFILE (270 degrees): the character faces exactly toward the RIGHT edge of the image, "
                                    "pure side view, only the character's RIGHT side visible, nose pointing right, one eye visible"),
        ("front_left", "3/4 esquerda", "THREE-QUARTER FRONT VIEW (315 degrees): the character's body is rotated 45 degrees so the "
                                       "character faces toward the RIGHT side of the image; the viewer sees the front and the "
                                       "character's RIGHT side; face visible in three-quarter"),
    ],
    "product": [
        ("front", "Frente", "FRONT VIEW (0 degrees): the main label/face of the product faces the camera directly"),
        ("front_right", "3/4 direita", "THREE-QUARTER VIEW (45 degrees): the product rotated 45 degrees, front and its left side visible"),
        ("right", "Lateral direita", "SIDE VIEW (90 degrees): only the product's side visible, front label not visible"),
        ("back", "Traseira", "BACK VIEW (180 degrees): the back of the product faces the camera, front label not visible"),
        ("left", "Lateral esquerda", "SIDE VIEW (270 degrees): only the product's other side visible"),
        ("front_left", "3/4 esquerda", "THREE-QUARTER VIEW (315 degrees): the product rotated 45 degrees the other way"),
        ("top", "Topo", "TOP VIEW: seen from directly above"),
        ("detail", "Detalhe", "MACRO DETAIL: close-up of the label, logo and materials"),
    ],
}

POSE_RULES = ("RULES: exactly ONE {what} in ONE single view filling the frame{full}; no turnaround sheet, no multiple views, "
              "no split panels, no collage, no side-by-side copies; no text, no labels, no captions, no watermark, no border. "
              "Plain neutral light-grey seamless background, soft even studio lighting, photorealistic.")

POSE_TEMPLATE = (
    "One single photograph showing ONE view of the {kind_word}. Subject: {name}: {description}. {attributes}. {references} "
    "THE VIEW TO GENERATE NOW: {view}. {consistency} {rules} {extra}"
)


CONSISTENCY = ("Same {what} as the reference images (the previous view and the front view): identical {traits}; only the "
               "viewing angle changes, rotating the {what} to the new view. Plain neutral light-grey background, consistent soft "
               "studio lighting, subject centered, same scale and framing as the references.")

SUGGEST_SYSTEM = (
    "You help an art director iterate on image prompts for a character or product turnaround. Given the current "
    "prompt and the view, propose 4 DIFFERENT improved alternatives that keep the same subject and view but fix "
    "common problems or explore variations (stricter consistency, better pose clarity, lighting, framing, "
    "expression or material detail). Keep each under 90 words, English. Reply JSON: "
    '{"suggestions":[{"label_pt":"2-4 words in Portuguese","why_pt":"short reason in Portuguese","prompt_en":"..."}]}'
)


def directions(kind: str) -> list[dict]:
    return [{"key": k, "label": l, "view": v} for k, l, v in DIRECTIONS.get(kind, DIRECTIONS["character"])]


MAX_REFS = 10  # limite seguro dos modelos de edição (Gemini/Seedream aceitam ~10)


def pose_refs(asset: dict, master_url: str | None, extra_urls: list[str] = (), prev_url: str | None = None,
              prev_label: str = "") -> list[dict]:
    """Ordem fixa: posição anterior (passo da rotação) -> frente (âncora) -> referências do item -> extras. Sem duplicatas."""
    out, seen = [], set()
    what = "character" if asset["kind"] == "character" else "product"
    items = []
    if prev_url and prev_url != master_url:
        items.append({"url": prev_url, "note": f"PREVIOUS VIEW ({prev_label or 'previous angle'}) of this exact {what}: "
                                               "same design, rotate it one step to the new view"})
    if master_url:
        items.append({"url": master_url, "note": f"FRONT VIEW: the approved design of this exact {what}; copy every detail"})
    items += [{"url": r["url"], "note": r.get("note") or "reference"} for r in asset.get("refs", [])]
    items += [{"url": u, "note": "extra reference"} for u in extra_urls]
    for it in items:
        if it["url"] and it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out[:MAX_REFS - 1]  # 1 vaga fica para a guia de pose


GUIDE_NOTE = {
    "character": "POSE GUIDE (NOT the character): a plain grey mannequin showing ONLY the exact body and head orientation "
                 "to reproduce; ignore its look, colors and shapes completely",
    "product": "ORIENTATION GUIDE (NOT the product): a plain box showing ONLY the rotation angle to reproduce (the blue "
               "face marks the product's front); ignore its look and colors completely",
}


def guide_ref(user: dict, kind: str, key: str) -> dict | None:
    src = guides.guide_path(kind, key)
    if not src:
        return None
    dest = storage.user_dir(user["id"], "guides") / src.name
    if not dest.exists():
        shutil.copyfile(src, dest)
    return {"url": storage.to_url(dest), "note": GUIDE_NOTE[kind]}


def pose_prompt(asset: dict, key: str, extra: str = "", with_master: bool = True, refs: list[dict] | None = None) -> str:
    d = {k: v for k, _, v in DIRECTIONS[asset["kind"]]}
    view = d.get(key, key)
    if refs is None:
        refs = pose_refs(asset, "master" if with_master and key != "front" else None)
    char = asset["kind"] == "character"
    what = "character" if char else "product"
    cons = ""
    if key != "front" and refs:
        cons = CONSISTENCY.format(what=what, traits="face, hairstyle, body, outfit, colors and logos" if char
                                  else "shape, proportions, materials, colors, label and logo")
    attrs = "; ".join(f"{k}: {v}" for k, v in (asset.get("attributes") or {}).items() if str(v).strip())
    ref_txt = (" ".join(f"Image {i}: {r['note']}." for i, r in enumerate(refs, 1)))
    if ref_txt:
        ref_txt = f"Reference images (use them exactly): {ref_txt}"
    rules = POSE_RULES.format(what="person" if char else "product",
                              full=", full body from head to toe visible with margin" if char and key != "detail" else "")
    return prompts.fill("pose_view", kind_word=what, name=asset["name"], description=asset.get("description") or "",
                        attributes=attrs, references=ref_txt, view=view, consistency=cons, rules=rules, extra=extra)


class PoseRender(BaseModel):
    key: str
    prompt: str | None = None        # prompt editado pelo usuário (None = padrão da posição)
    master_url: str | None = None    # imagem aprovada da frente (obrigatória nas outras posições)
    prev_url: str | None = None      # posição anterior na rotação (geração em cadeia)
    prev_label: str = ""
    extra_refs: list[str] = Field(default_factory=list)
    model: str | int | None = "auto"
    aspect: str = "1:1"
    name: str = ""
    extra: str = ""                  # correção vinda da verificação automática


def render_pose(user: dict, asset: dict, body: PoseRender) -> dict:
    from . import jobs
    if body.key != "front" and not body.master_url:
        raise ValueError("gere a posição de frente (master) antes das outras")
    refs = pose_refs(asset, body.master_url if body.key != "front" else None, body.extra_refs,
                     body.prev_url if body.key != "front" else None, body.prev_label)
    g = guide_ref(user, asset["kind"], body.key)
    has_identity = bool(refs)  # sem nenhuma referência de aparência, a guia não pode virar "o personagem"
    if g:
        refs.append(g)
    for r in refs:
        storage.resolve(r["url"], user)
    if not refs and body.key != "front":
        raise ValueError("gere a frente antes: ela é a referência das outras posições")
    refs = list(refs)
    if not has_identity and not (asset.get("description") or "").strip():
        raise ValueError("descreva o personagem/produto ou ligue fotos de referência")
    prompt = body.prompt or pose_prompt(asset, body.key, refs=refs, extra=body.extra)
    if body.prompt and body.extra:
        prompt = f"{prompt} {body.extra}"
    # obrigatório: o modelo sempre recebe a legenda de TODAS as referências, mesmo com prompt editado
    if refs:
        ref_block = "Reference images (use them exactly): " + " ".join(f"Image {i}: {r['note']}." for i, r in enumerate(refs, 1))
        if "Image 1:" not in prompt:
            prompt = f"{ref_block} {prompt}"
    job = jobs.submit(user, jobs.RenderRequest(
        node_id=f"asset:{asset['id']}:{body.key}", name=body.name or f"{asset['name']} · {body.key}", gen="image",
        model=body.model, prompt=prompt, keyframe_prompt=prompt, aspect=body.aspect,
        ref_image_urls=[r["url"] for r in refs], adapt=False,
        require_refs=True))  # sempre com referências (no mínimo a guia de pose)
    return {"job": job, "refs": refs, "prompt": prompt}


class SuggestBody(BaseModel):
    prompt: str
    view: str = ""
    kind: str = "character"
    name: str = ""
    feedback: str = ""


def suggest(body: SuggestBody) -> dict:
    data = llm.chat_json(prompts.get("suggest"), "\n".join(x for x in [
        f"Subject: {body.kind} named '{body.name}'.", f"View: {body.view}.",
        f"What the user did not like (pt-BR): {body.feedback}" if body.feedback else "",
        f"Current prompt: {body.prompt}"] if x))
    items = (data or {}).get("suggestions") or []
    items = [i for i in items if isinstance(i, dict) and i.get("prompt_en")][:4]
    if items:
        return {"suggestions": items, "source": llm.source()}
    base = body.prompt.rstrip(". ")
    return {"source": "regras", "suggestions": [
        {"label_pt": "Mais consistente", "why_pt": "reforça que é o mesmo do master",
         "prompt_en": f"{base}. Strictly identical to reference image 1, do not change any detail of the design."},
        {"label_pt": "Pose mais clara", "why_pt": "silhueta e direção bem legíveis",
         "prompt_en": f"{base}. Clear readable silhouette, neutral standing pose, arms slightly away from the body."},
        {"label_pt": "Luz de estúdio", "why_pt": "iluminação uniforme sem sombras duras",
         "prompt_en": f"{base}. Even diffused studio lighting, no harsh shadows, accurate colors."},
        {"label_pt": "Enquadrar inteiro", "why_pt": "evita cortar pés/topo",
         "prompt_en": f"{base}. Entire subject fully visible with margin around it, nothing cropped."},
    ]}


class SheetBody(BaseModel):
    urls: list[str]
    labels: list[str] = Field(default_factory=list)
    cols: int = 0  # 0 = automático
    cell: int = 512
    pad: int = 16
    bg: str = "transparent"  # transparent | white | grey | black
    show_labels: bool = True


BG = {"transparent": (0, 0, 0, 0), "white": (255, 255, 255, 255), "grey": (220, 222, 226, 255), "black": (12, 12, 14, 255)}


def build_sheet(user: dict, body: SheetBody) -> dict:
    if not body.urls:
        raise ValueError("ligue as posições no nó Spritesheet")
    paths = [storage.resolve(u, user) for u in body.urls[:32]]
    n = len(paths)
    cols = body.cols or (4 if n > 4 else n)
    rows = math.ceil(n / cols)
    cell, pad = max(64, min(1024, body.cell)), max(0, min(64, body.pad))
    label_h = 28 if body.show_labels else 0
    W, H = cols * cell + (cols + 1) * pad, rows * (cell + label_h) + (rows + 1) * pad
    sheet = Image.new("RGBA", (W, H), BG.get(body.bg, BG["transparent"]))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    ink = (20, 20, 24, 255) if body.bg in ("white", "grey") else (235, 235, 240, 255)
    frames = []
    for i, p in enumerate(paths):
        img = Image.open(p).convert("RGBA")
        img.thumbnail((cell, cell))
        c, r = i % cols, i // cols
        x0, y0 = pad + c * (cell + pad), pad + r * (cell + label_h + pad)
        sheet.alpha_composite(img, (x0 + (cell - img.width) // 2, y0 + (cell - img.height) // 2))
        label = body.labels[i] if i < len(body.labels) else ""
        if body.show_labels and label:
            tw = draw.textlength(label, font=font)
            draw.text((x0 + (cell - tw) / 2, y0 + cell + 4), label, fill=ink, font=font)
        frames.append({"label": label, "x": x0, "y": y0, "w": cell, "h": cell})
    dest = storage.user_dir(user["id"], "renders") / f"spritesheet_{uuid.uuid4().hex[:6]}.png"
    sheet.save(dest, "PNG")
    # mapa dos quadros (útil para engines de jogo / animação)
    dest.with_suffix(".json").write_text(json.dumps({"image": dest.name, "cols": cols, "rows": rows, "frames": frames}, ensure_ascii=False), encoding="utf-8")
    return {**media.describe(dest), "cols": cols, "rows": rows, "map_url": storage.to_url(dest.with_suffix(".json"))}
