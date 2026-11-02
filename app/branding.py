"""Branding do app: nome, logo, tema de cores e estilo (pixel/arredondado)."""
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import HTTPException

from . import db
from .config import APP_NAME, DATA_DIR

BRAND_DIR = DATA_DIR / "branding"
BRAND_DIR.mkdir(parents=True, exist_ok=True)
LOGO_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}

THEMES = {
    "emerald": {"label": "Esmeralda", "accent": "#10b981", "accent2": "#22d3ee", "bg": "#08090d"},
    "violet": {"label": "Violeta", "accent": "#a855f7", "accent2": "#f472b6", "bg": "#0a0810"},
    "cyan": {"label": "Ciano", "accent": "#22d3ee", "accent2": "#60a5fa", "bg": "#070b10"},
    "amber": {"label": "Âmbar", "accent": "#f59e0b", "accent2": "#f97316", "bg": "#0d0a06"},
    "rose": {"label": "Rosa", "accent": "#f43f5e", "accent2": "#fb923c", "bg": "#0e0709"},
    "mono": {"label": "Monocromático", "accent": "#e5e7eb", "accent2": "#9ca3af", "bg": "#09090b"},
    "gameboy": {"label": "Game Boy", "accent": "#9bbc0f", "accent2": "#8bac0f", "bg": "#0f1a0f"},
}
DEFAULT = {"app_name": None, "theme": "emerald", "accent": None, "accent2": None, "bg": None,
           "style": "pixel", "logo": None, "tagline": "Entre para abrir o seu estúdio."}


def get() -> dict:
    b = {**DEFAULT, **(db.get_setting("branding") or {})}
    t = THEMES.get(b["theme"], THEMES["emerald"])
    b["app_name"] = db.get_setting("app_name") or APP_NAME
    b["colors"] = {"accent": b["accent"] or t["accent"], "accent2": b["accent2"] or t["accent2"], "bg": b["bg"] or t["bg"]}
    b["logo_url"] = f"/branding/{b['logo']}" if b.get("logo") else None
    return b


def save(data: dict) -> dict:
    cur = {**DEFAULT, **(db.get_setting("branding") or {})}
    if data.get("app_name"):
        db.set_setting("app_name", str(data["app_name"]).strip()[:60])
    for k in ("theme", "accent", "accent2", "bg", "style", "tagline"):
        if k in data:
            v = data[k]
            cur[k] = (str(v).strip()[:80] or None) if v is not None else None
    for k in ("accent", "accent2", "bg"):  # só cores #rrggbb
        if cur.get(k) and not re.fullmatch(r"#[0-9a-fA-F]{6}", cur[k]):
            cur[k] = None
    if cur["theme"] not in THEMES:
        cur["theme"] = "emerald"
    if cur["style"] not in ("pixel", "smooth"):
        cur["style"] = "pixel"
    db.set_setting("branding", cur)
    return get()


# SVG é XML com script: só aceita SVG "estático" (sem script, handlers, foreignObject, DOCTYPE ou recursos externos).
_SVG_BLOCKED = [
    r"<script", r"<foreignobject", r"<iframe", r"<embed", r"<object", r"<!doctype", r"<!entity", r"javascript:",
    r"[\s\"'/]on[a-z]+\s*=",                       # onload=, onclick=...
    r"href\s*=\s*[\"']\s*(?!#)",                  # href externo (ou data:)
    r"url\(\s*[\"']?\s*(?!#)",                      # url(...) externo em style/fill
]


def check_svg(data: bytes) -> None:
    text = data.decode("utf-8", "replace").lower()
    if any(re.search(p, text) for p in _SVG_BLOCKED):
        raise HTTPException(400, "SVG com script, handlers ou recursos externos não é aceito")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise HTTPException(400, "SVG inválido") from e
    if not root.tag.endswith("svg"):
        raise HTTPException(400, "SVG inválido")


def save_logo(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in LOGO_EXT or len(data) > 4 * 2**20:
        raise HTTPException(400, "Logo precisa ser PNG, JPG, WEBP, SVG ou GIF até 4 MB")
    if ext == ".svg":
        check_svg(data)
    cur = {**DEFAULT, **(db.get_setting("branding") or {})}
    if cur.get("logo"):
        (BRAND_DIR / cur["logo"]).unlink(missing_ok=True)
    name = f"logo_{uuid.uuid4().hex[:6]}{ext}"
    (BRAND_DIR / name).write_bytes(data)
    cur["logo"] = name
    db.set_setting("branding", cur)
    return get()


def remove_logo() -> dict:
    cur = {**DEFAULT, **(db.get_setting("branding") or {})}
    if cur.get("logo"):
        (BRAND_DIR / cur["logo"]).unlink(missing_ok=True)
    cur["logo"] = None
    db.set_setting("branding", cur)
    return get()


def logo_path(name: str) -> Path:
    p = (BRAND_DIR / name).resolve()
    if not p.is_relative_to(BRAND_DIR.resolve()) or not p.is_file():
        raise HTTPException(404)
    return p
