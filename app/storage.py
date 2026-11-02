"""Arquivos isolados por usuário: DATA_DIR/users/<id>/{uploads,renders,cuts}.

URLs públicas têm a forma /files/<id>/<pasta>/<arquivo> e só abrem para o dono (ou admin).
"""
import base64
import binascii
import secrets
import shutil
import time
from pathlib import Path

from fastapi import HTTPException

from .auth import CURRENT
from .config import USERS_DIR

SUBDIRS = ("uploads", "renders", "cuts")
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def user_dir(uid: int, sub: str | None = None) -> Path:
    d = USERS_DIR / str(uid)
    if sub:
        d = d / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_url(path: Path) -> str:
    return "/files/" + path.resolve().relative_to(USERS_DIR.resolve()).as_posix()


def owner_of(url: str) -> int | None:
    parts = url.split("?")[0].strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "files" and parts[1].isdigit():
        return int(parts[1])
    return None


def resolve(url: str, user: dict | None = None, must_exist: bool = True) -> Path:
    """URL /files/... -> caminho local, garantindo que pertence ao usuário (admin vê tudo)."""
    user = user or CURRENT.get()
    if not url or not user:
        raise HTTPException(403, "Arquivo inválido")
    uid = owner_of(url)
    if uid is None or (uid != user["id"] and not user.get("is_admin", user.get("role") == "admin")):
        raise HTTPException(403, "Arquivo de outro usuário")
    rel = url.split("?")[0].strip("/").split("/", 1)[1]
    if any(part in ("", ".", "..") or "\\" in part for part in rel.split("/")):
        raise HTTPException(403, "Caminho inválido")
    path = (USERS_DIR / rel).resolve()
    if not path.is_relative_to((USERS_DIR / str(uid)).resolve()):  # nunca sai da pasta do dono
        raise HTTPException(403, "Caminho inválido")
    if must_exist and not path.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return path


def kind_of(path: Path) -> str:
    s = path.suffix.lower()
    return "video" if s in VIDEO_EXT else "audio" if s in AUDIO_EXT else "image" if s in IMAGE_EXT else "file"


# Pasta oculta de trabalho (máscaras, quadros anotados, imagens temporárias dos editores de desenho).
# Nunca aparece em Meus arquivos, não conta na cota e é apagada depois de TEMP_MAX_AGE_S.
TEMP_DIR = "_tmp"
TEMP_MAX_AGE_S = 24 * 3600
TEMP_MAX_BYTES = 8 * 2**20
IMAGE_MIME = {"data:image/png;base64,": ".png", "data:image/jpeg;base64,": ".jpg", "data:image/webp;base64,": ".webp"}


def temp_file(uid: int, suffix: str) -> Path:
    """Caminho novo dentro da pasta oculta do usuário (cria a pasta e limpa o que já venceu)."""
    cleanup_temp(uid)
    d = user_dir(uid, TEMP_DIR)
    return d / f"{secrets.token_hex(6)}{suffix}"


def save_temp_data_url(uid: int, data_url: str) -> Path:
    """Imagem em data URL (PNG/JPEG/WEBP) gravada na pasta oculta. Valida tipo e tamanho."""
    prefix = next((p for p in IMAGE_MIME if data_url.startswith(p)), None)
    if not prefix:
        raise HTTPException(400, "Imagem inválida (use PNG, JPEG ou WEBP)")
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, "Imagem inválida") from e
    if len(raw) > TEMP_MAX_BYTES:
        raise HTTPException(413, "Imagem grande demais")
    path = temp_file(uid, IMAGE_MIME[prefix])
    path.write_bytes(raw)
    return path


def cleanup_temp(uid: int, max_age_s: float = TEMP_MAX_AGE_S) -> int:
    """Apaga arquivos da pasta oculta mais velhos que max_age_s. Devolve quantos foram removidos."""
    d = USERS_DIR / str(uid) / TEMP_DIR
    if not d.is_dir():
        return 0
    now, removed = time.time(), 0
    for f in d.iterdir():
        if f.is_file() and now - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def used_bytes(uid: int) -> int:
    d = USERS_DIR / str(uid)
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and TEMP_DIR not in f.relative_to(d).parts) if d.exists() else 0


def check_quota(user: dict, extra: int = 0):
    lim = user["limits"].get("storage_mb")
    if lim is not None and used_bytes(user["id"]) + extra > lim * 2**20:
        raise HTTPException(402, f"Espaço esgotado ({lim} MB). Apague arquivos antigos.")


# Arquivos derivados (miniatura, último frame) aparecem agrupados ao arquivo principal.
DERIVED = ("_first.png", "_last.png")


def list_files(uid: int | None = None) -> list[dict]:
    roots = [USERS_DIR / str(uid)] if uid is not None else [p for p in USERS_DIR.iterdir() if p.is_dir()]
    out = []
    for root in roots:
        if not root.exists():
            continue
        cleanup_temp(int(root.name))
        for f in root.rglob("*"):
            if not f.is_file() or f.name.endswith(DERIVED) or f.parent.name == "guides" or TEMP_DIR in f.relative_to(root).parts:
                continue
            st = f.stat()
            if f.suffix.lower() == ".gif" and not f.with_name(f"{f.stem}_first.png").exists():
                try:
                    from .media import gif_thumb
                    gif_thumb(f)
                except Exception:  # noqa: BLE001 - miniatura é opcional
                    pass
            elif kind_of(f) == "video" and not f.with_name(f"{f.stem}_first.png").exists():
                try:  # vídeos antigos (ex.: cortes feitos antes da miniatura) ganham o 1º quadro na primeira listagem
                    from .media import extract_frame
                    extract_frame(f, last=False)
                except Exception:  # noqa: BLE001 - miniatura é opcional
                    pass
            out.append({"url": to_url(f), "name": f.name, "folder": f.parent.name, "kind": kind_of(f),
                        "size": st.st_size, "mtime": st.st_mtime, "user_id": int(root.name)})
    return sorted(out, key=lambda x: -x["mtime"])


def delete(url: str, user: dict | None = None) -> int:
    path = resolve(url, user)
    freed = 0
    for p in [path, *(path.with_name(path.stem + d) for d in DERIVED)]:
        if p.exists():
            freed += p.stat().st_size
            p.unlink()
    return freed


def delete_user_files(uid: int) -> int:
    d = USERS_DIR / str(uid)
    n = used_bytes(uid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return n
