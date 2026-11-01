"""Login por sessão (cookie HttpOnly), usuário corrente e bootstrap do primeiro admin."""
import json
import secrets
import time
from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request

from . import db, groups
from .config import ADMIN_EMAIL, ADMIN_PASSWORD, SESSION_DAYS
from .security import hash_password, new_token, token_hash, verify_password

COOKIE = "studio_session"
# Usuário da requisição/job atual: o registro de uso e o isolamento de arquivos leem daqui.
CURRENT: ContextVar[dict | None] = ContextVar("current_user", default=None)
# Idioma da interface de quem fez a requisição (header X-Lang): a IA escreve os textos para o usuário nele.
LANG: ContextVar[str] = ContextVar("ui_lang", default="en")
LANG_NAMES = {"en": "English", "pt": "Brazilian Portuguese", "es": "Spanish"}

DEFAULT_LIMITS = {
    "tokens_month": 2500,     # gasto por mês em tokens (1 US$ = token_rate tokens; padrão 100)
    "storage_mb": 5000,       # espaço em disco
    "models": None,           # None = todos os modelos habilitados; lista de ids = só esses
}


def default_limits() -> dict:
    saved = db.get_setting("default_limits") or {}
    return {**DEFAULT_LIMITS, **{k: v for k, v in saved.items() if k in DEFAULT_LIMITS}}


def public_user(u: dict) -> dict:
    eff = groups.effective(u["id"], json.loads(u["limits"] or "{}"), default_limits())
    perms = eff["permissions"]
    is_admin = u["role"] == "admin" or perms["admin"]
    if is_admin:
        perms = {k: True for k in perms}
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "is_admin": is_admin,
            "active": bool(u["active"]), "limits": {k: v for k, v in eff["limits"].items() if k in DEFAULT_LIMITS}, "permissions": perms, "groups": eff["groups"],
            "auth_provider": u.get("auth_provider") or "local",
            "created_at": u["created_at"], "last_login": u["last_login"]}


def bootstrap_admin():
    if db.one("SELECT id FROM users WHERE role = 'admin'"):
        return
    password = ADMIN_PASSWORD or secrets.token_urlsafe(12)
    db.run("INSERT INTO users(email, name, password_hash, role, limits, created_at) VALUES(?,?,?,?,?,?)",
           (ADMIN_EMAIL.lower(), "Administrador", hash_password(password), "admin",
            json.dumps({k: None for k in DEFAULT_LIMITS}), time.time()))
    print("=" * 60)
    print(f"Admin criado: {ADMIN_EMAIL}")
    if not ADMIN_PASSWORD:
        print(f"Senha gerada: {password}   (troque no painel; ou defina ADMIN_PASSWORD)")
    print("=" * 60)


# Tentativas de login por e-mail/IP (força bruta).
_attempts: dict[str, list[float]] = {}


def _throttle(key: str):
    t = time.time()
    hits = [x for x in _attempts.get(key, []) if t - x < 600]
    _attempts[key] = hits
    if len(hits) >= 10:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    hits.append(t)


def login(email: str, password: str, ip: str) -> tuple[str, dict]:
    email = email.strip().lower()
    _throttle(f"{ip}|{email}")
    u = db.one("SELECT * FROM users WHERE email = ?", (email,))
    if u and u["password_hash"].startswith("!"):
        raise HTTPException(401, "Esta conta entra pelo botão da Microsoft")
    if not u or not verify_password(password, u["password_hash"]):
        raise HTTPException(401, "E-mail ou senha incorretos")
    if not u["active"]:
        raise HTTPException(403, "Conta desativada. Fale com o administrador.")
    _attempts.pop(f"{ip}|{email}", None)
    return create_session(u["id"]), u


def create_session(uid: int) -> str:
    token = new_token()
    t = time.time()
    db.run("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
           (token_hash(token), uid, t, t + SESSION_DAYS * 86400))
    db.run("UPDATE users SET last_login = ? WHERE id = ?", (t, uid))
    db.run("DELETE FROM sessions WHERE expires_at < ?", (t,))
    return token


def logout(token: str | None):
    if token:
        db.run("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    u = db.one("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
               "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1", (token_hash(token), time.time()))
    return public_user(u) if u else None


async def current_user(request: Request) -> dict:
    # async: o ContextVar setado aqui vale para o endpoint (sync deps rodam em outra thread).
    u = user_from_request(request)
    if not u:
        raise HTTPException(401, "Faça login")
    CURRENT.set(u)
    return u


async def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Apenas administradores")
    return user


PERM_MSG = {k: label for k, label, _ in groups.PERMISSIONS}


def require(user: dict, perm: str):
    if not user["permissions"].get(perm):
        raise HTTPException(403, f"Sem permissão: {PERM_MSG.get(perm, perm)}. Fale com o administrador.")


def need(perm: str):
    """Dependência de rota: exige uma permissão de grupo."""
    async def dep(user: dict = Depends(current_user)) -> dict:
        require(user, perm)
        return user
    return dep
