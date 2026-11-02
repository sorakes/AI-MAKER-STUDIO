"""Prompts por trás de cada nó, editáveis no painel admin (padrão em inglês: os modelos entendem melhor).

O texto padrão continua no módulo de cada nó; aqui ficam o catálogo e as substituições salvas pelo admin.
Variáveis no formato {nome} são preenchidas pelo app (lista em `vars` de cada prompt).
"""
from . import db

CATALOG = {
    "autoprompt": {"node": "Gerar vídeo / imagem", "label": "Prompt técnico do plano",
                   "help": "Transforma a intenção + câmera + referências no prompt de vídeo e de still.",
                   "vars": []},
    "adapt_frame": {"node": "Gerar vídeo", "label": "Ajuste ao 1º frame",
                    "help": "Depois que o still existe, reescreve o prompt de vídeo para bater com a imagem real.", "vars": []},
    "direction": {"node": "Câmera e movimento", "label": "Leitura do desenho no frame",
                  "help": "A IA de visão recebe o frame com o desenho (roxo = câmera, cores = elementos) e as notas.", "vars": []},
    "sketch_edit": {"node": "Gerar imagem", "label": "Desenho para regerar",
                    "help": "A IA de visão lê as marcações sobre a imagem (regiões, linhas e instruções por cor) e escreve a instrução de edição.",
                    "vars": []},
    "director": {"node": "Roteiro", "label": "Decupagem do brief", "help": "Divide o brief em planos com câmera e duração.", "vars": []},
    "transition": {"node": "Transição", "label": "Plano de transição A→B", "help": "Planeja a passagem de câmera entre dois planos.", "vars": []},
    "caption": {"node": "Visão", "label": "Legenda de imagem", "help": "Descreve imagens (1º frame, referências) para os prompts.", "vars": []},
    "pose_view": {"node": "Posições", "label": "Prompt de cada posição",
                  "help": "Monta o prompt de cada uma das 8 posições (vista, consistência e regras: uma figura, sem texto).",
                  "vars": ["kind_word", "name", "description", "attributes", "references", "view", "consistency", "rules", "extra"]},
    "suggest": {"node": "Posições (regerar)", "label": "Sugestões de prompt",
                "help": "Ao regerar uma posição, propõe prompts alternativos para escolher.", "vars": []},
    "character_sheet": {"node": "Personagens", "label": "Geração de personagem",
                        "help": "Prompt de imagem para gerar o estudo do personagem a partir da ficha e das referências.",
                        "vars": ["name", "description", "attributes", "references", "view", "extra"]},
    "product_sheet": {"node": "Produtos", "label": "Geração de produto",
                      "help": "Prompt de imagem para gerar o estudo do produto a partir da ficha e das referências.",
                      "vars": ["name", "description", "attributes", "references", "view", "extra"]},
}

ASSET_DEFAULTS = {
    "character_sheet": (
        "Character study of {name}: {description}. {attributes}. {references} {view}. Keep the face, body type, "
        "hairstyle and skin tone perfectly consistent; clothing and accessories exactly as in the reference images, "
        "logos and printed text reproduced exactly. Neutral light-grey studio background, soft even lighting, "
        "photorealistic, high detail, no extra people, no text overlays. {extra}"
    ),
    "product_sheet": (
        "Product study of {name}: {description}. {attributes}. {references} {view}. The product must match the "
        "reference images exactly: same shape, proportions, materials, colors, label and logo with exact spelling. "
        "Clean seamless studio background, professional product photography lighting with soft reflections, "
        "photorealistic, high detail, no extra objects, no text overlays. {extra}"
    ),
}


def _defaults() -> dict[str, str]:
    from . import direction, director, promptgen, sketch, transitions  # import tardio: evita ciclo
    from .llm import CAPTION_PROMPT
    from .sprites import POSE_TEMPLATE, SUGGEST_SYSTEM
    return {"suggest": SUGGEST_SYSTEM, "pose_view": POSE_TEMPLATE, "autoprompt": promptgen.SYSTEM, "adapt_frame": promptgen.ADAPT_SYSTEM, "direction": direction.SYSTEM,
            "director": director.SYSTEM, "transition": transitions.SYSTEM, "caption": CAPTION_PROMPT,
            "sketch_edit": sketch.SYSTEM, **ASSET_DEFAULTS}


def overrides() -> dict:
    return db.get_setting("prompts") or {}


def get(key: str) -> str:
    return overrides().get(key) or _defaults()[key]


def listing() -> list[dict]:
    d, o = _defaults(), overrides()
    return [{"key": k, **meta, "default": d[k], "text": o.get(k) or d[k], "custom": bool(o.get(k))} for k, meta in CATALOG.items()]


def save(key: str, text: str | None):
    if key not in CATALOG:
        raise KeyError(key)
    o = overrides()
    if text is None or text.strip() == "" or text.strip() == _defaults()[key].strip():
        o.pop(key, None)
    else:
        o[key] = text
    db.set_setting("prompts", o)


def fill(key: str, **values) -> str:
    """Preenche {variáveis}; chaves desconhecidas ficam vazias em vez de quebrar."""
    class Safe(dict):
        def __missing__(self, k):
            return ""
    import re
    text = " ".join(get(key).format_map(Safe(values)).split())
    return re.sub(r"(\s*\.\s*){2,}", ". ", text).strip()
