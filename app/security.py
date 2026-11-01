"""Senhas (scrypt), tokens de sessão e criptografia das API keys (Fernet / AES-128-CBC + HMAC)."""
import base64
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from .config import SECRET_KEY

_FERNET = Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"api-keys:{SECRET_KEY}".encode()).digest()))


def encrypt(value: str) -> str:
    return _FERNET.encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None  # SECRET_KEY mudou: a key precisa ser cadastrada de novo


def mask(key: str | None) -> str:
    if not key:
        return ""
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else "…" + key[-2:]


_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
