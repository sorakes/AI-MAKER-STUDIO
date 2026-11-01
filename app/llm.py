"""Texto e visão (prompts automáticos, legendas de imagem) com o modelo padrão do painel."""
import json
from pathlib import Path

from . import media, prompts, providers, registry, usage
from .auth import CURRENT, LANG, LANG_NAMES


def _model(kind: str) -> dict | None:
    u = CURRENT.get()
    pool = registry.for_user(u, kind) if u else [m for m in registry.list_models(kind) if m["available"]]
    pool = [m for m in pool if m["provider"] == "openrouter"]
    pool.sort(key=lambda m: not m["is_default"])
    return pool[0] if pool else None


def online() -> bool:
    return _model("text") is not None


def source() -> str:
    m = _model("text")
    return m["label"] if m else "regras"


def chat(messages: list[dict], kind: str = "text", as_json: bool = False, timeout: float = 90) -> str | None:
    m = _model(kind) or (_model("text") if kind == "vision" else None)
    if not m:
        return None
    lang = LANG.get()
    if lang != "pt":  # instruções pedem pt-BR nos campos para o usuário: troca pelo idioma da interface
        messages = [{"role": "system", "content": (
            f"Language rule: write every human-readable text value meant for the user (titles, names, intents, notes, "
            f"explanations, warnings, labels; including JSON fields whose names end in _pt or that mention pt-BR or "
            f"Portuguese) in {LANG_NAMES[lang]}. Prompts meant for image/video models stay in English.")}] + messages
    try:
        msg, cost = providers.openrouter_chat(m["model_id"], messages, as_json=as_json, timeout=timeout, key_id=m.get("key_id"))
        usage.log(kind, provider=m["provider"], model=m["model_id"], cost=cost, detail={"key": m.get("key_name") or ""})
        return msg.get("content")
    except providers.ProviderError as e:
        usage.log(kind, provider=m["provider"], model=m["model_id"], status="error", detail=str(e))
        return None


def chat_json(system: str, user: str) -> dict | None:
    raw = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], as_json=True)
    if not raw:
        return None
    try:
        return json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        return None


CAPTION_PROMPT = ("Describe this image for a video generation model in ONE dense English sentence: "
                  "subject, materials, lighting, colors, setting, composition. No preamble.")


def describe_image(path: Path) -> str | None:
    """Legenda visual objetiva (em inglês): o prompt de vídeo precisa bater com o 1º frame."""
    return chat([{"role": "user", "content": [
        {"type": "text", "text": prompts.get("caption")},
        {"type": "image_url", "image_url": {"url": media.data_url(path)}},
    ]}], kind="vision", timeout=60)
