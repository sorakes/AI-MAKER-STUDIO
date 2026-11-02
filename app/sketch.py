"""Regerar uma imagem a partir de marcações feitas sobre ela (regiões circuladas, linhas e instruções por cor).

A imagem anotada vai SÓ para o modelo de visão, que transforma as marcações numa instrução de edição.
O gerador recebe a imagem ORIGINAL como referência + o texto de edição: os traços nunca entram no resultado.
Sem modelo de visão, a instrução é montada com os textos das cores + a geometria dos traços.
"""
import json

from pydantic import BaseModel, Field

from . import direction, jobs, llm, prompts

SYSTEM = (
    "You are an image editor's assistant. You receive an ORIGINAL picture and the SAME picture with the art director's "
    "marks drawn over it, plus one note per color. Each color is one instruction: a closed loop circles a REGION that "
    "must change; an open line starts at a solid DOT and ends at a hollow RING (the arrow-free marks show where an "
    "element starts and where it must go). Look at the picture to identify exactly WHAT is circled or marked by each "
    "color, then write ONE precise edit instruction for an image model that receives the ORIGINAL picture as image 1. "
    "The marks themselves are NOT part of the result: never ask for lines, arrows, circles or labels in the image. "
    'Reply JSON: {"edit_en":"precise English edit instruction naming the regions and the change for each color",'
    '"keep_en":"what must stay identical",'
    '"notes_pt":["avisos curtos em português, se houver contradições"]}'
)


class SketchRequest(BaseModel):
    node_id: str = ""
    name: str = "Imagem"
    source_url: str  # imagem atual do nó (/files/...): é a referência da edição
    annotated: str  # data URL: a imagem com as marcações (só para a visão, nunca vai ao gerador)
    layers: list[direction.Layer] = Field(default_factory=list)
    intent: str = ""
    model: str | int | None = "auto"
    aspect: str = "16:9"
    quality: str = "draft"
    ref_image_urls: list[str] = Field(default_factory=list)  # referências do nó (logo, produto...)


def active_layers(req: SketchRequest) -> list[direction.Layer]:
    return [l for l in req.layers if l.shapes or l.prompt.strip()]


def notes(req: SketchRequest) -> list[str]:
    out = [f"Edit intent (pt-BR): {req.intent}" if req.intent else ""]
    for l in active_layers(req):
        geo = direction._geometry(l) or "nothing marked, only the note"
        out.append(f"{direction.COLOR_NAMES.get(l.color, l.color).upper()} ({l.label}) instruction: "
                   f"{l.prompt or '(no note)'}; marks: {geo}")
    return [n for n in out if n]


def _rules_edit(req: SketchRequest) -> str:
    """Sem visão: a instrução é a soma dos textos de cada cor + onde estão as marcações."""
    return " ".join(f"{direction.COLOR_NAMES.get(l.color, l.color).capitalize()} marks: "
                    f"{l.prompt or 'change this area'} ({direction._geometry(l) or 'the marked area'})."
                    for l in active_layers(req))


def plan_edit(req: SketchRequest) -> dict:
    """Visão lê a imagem anotada e escreve a instrução de edição. Falha de visão cai para as regras."""
    raw = llm.chat([{"role": "system", "content": prompts.get("sketch_edit")}, {"role": "user", "content": [
        {"type": "text", "text": "\n".join(notes(req))},
        {"type": "image_url", "image_url": {"url": direction._shrink(req.annotated)}},
    ]}], kind="vision", as_json=True, timeout=90)
    data = None
    if raw:
        try:
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        except ValueError:
            data = None
    if data and data.get("edit_en"):
        return {"edit_en": data["edit_en"], "keep_en": data.get("keep_en") or "",
                "notes_pt": data.get("notes_pt") or [], "source": llm.source()}
    return {"edit_en": _rules_edit(req), "keep_en": "",
            "notes_pt": ["Sem modelo de visão configurado: a IA não olhou a imagem, só os textos e a posição das marcações."],
            "source": "regras"}


def edit_prompt(edit_en: str, keep_en: str = "", annotated: bool = False) -> str:
    """Prompt do gerador: a imagem 1 é a ORIGINAL; nada de traços no resultado.

    Com annotated, a imagem 2 é a mesma foto com as marcações desenhadas: rotulada, como guia, e nunca reproduzida."""
    keep = keep_en or "composition, framing, lighting, people, products and logos"
    labels = ("Image 1: original photo. Image 2: the same photo with the drawn marks showing where and how to edit. "
              "Use the marks only as a guide. " if annotated else "Edit image 1 (the ORIGINAL picture) and return the edited picture. ")
    return (f"{labels}Apply exactly these changes: {edit_en} "
            f"Keep everything else identical to image 1: {keep}. The result must be a clean photograph with NO drawn "
            "marks: no lines, arrows, circles, dots, rings, labels or annotations of any kind.")


def build_request(req: SketchRequest, edit: dict, annotated_url: str | None = None) -> jobs.RenderRequest:
    """Vira um render de imagem comum: mesmas cotas, permissões e fila do Gerar imagem.

    O gerador recebe a original e, quando há, a imagem com as marcações (além do texto); a máscara/desenho não
    vira imagem separada. require_refs: sem modelo que aceite a imagem original, falha em vez de gerar só com texto."""
    prompt = edit_prompt(edit["edit_en"], edit.get("keep_en", ""), annotated=bool(annotated_url))
    refs = [req.source_url, *([annotated_url] if annotated_url else []), *req.ref_image_urls]
    return jobs.RenderRequest(node_id=req.node_id, name=req.name, gen="image", model=req.model, prompt=prompt,
                              keyframe_prompt=prompt, aspect=req.aspect, quality=req.quality,
                              ref_image_urls=refs, require_refs=True,
                              local_op={"images": ["original", "desenho"] if annotated_url else ["original"]})
