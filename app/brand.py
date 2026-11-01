"""Brand Kit e Brand Guard: identidade da marca do cliente e regras por categoria."""
from pydantic import BaseModel, Field


class BrandKit(BaseModel):
    brand: str = "Marca"
    product: str = "produto"
    category: str = "geral"  # geral | bebida_alcoolica | infantil | automotivo | beleza
    palette: str = ""
    mood: str = "premium, cinematográfico"
    forbidden: list[str] = Field(default_factory=list)


CATEGORY_RULES = {
    "bebida_alcoolica": {
        "blocked": ["child", "children", "kid", "kids", "teen", "teenager", "minor", "driving", "car keys",
                    "criança", "adolescente", "dirigindo"],
        "note": "Autorregulamentação publicitária de bebidas alcoólicas: sem menores, sem associação com direção; personagens devem aparentar 25+.",
        "force": "all people are adults over 25 years old",
    },
    "infantil": {
        "blocked": ["alcohol", "beer", "weapon", "cerveja", "arma"],
        "note": "Publicidade infantil: sem apelo de consumo direto à criança.",
        "force": "",
    },
}


PEOPLE_WORDS = ("people", "adult", "friend", "man ", "woman", "person", "crowd", "couple")


def brand_guard(prompt: str, kit: BrandKit) -> tuple[str, list[str]]:
    """Remove termos proibidos e aplica regras da categoria. Retorna prompt seguro + alertas."""
    warnings = []
    rule = CATEGORY_RULES.get(kit.category)
    blocked = list(kit.forbidden) + (rule["blocked"] if rule else [])
    words = prompt.split(" ")
    clean = [w for w in words if w.strip(",.").lower() not in {b.lower() for b in blocked}]
    if len(clean) != len(words):
        removed = {w.strip(",.") for w in words} - {w.strip(",.") for w in clean}
        warnings.append(f"Removido do prompt: {', '.join(sorted(removed))}")
    prompt = " ".join(clean)
    has_people = any(w in prompt.lower() for w in PEOPLE_WORDS)
    if rule and rule["force"] and has_people and rule["force"] not in prompt:
        prompt = f"{prompt}, {rule['force']}"
    return prompt, warnings
