"""Configuração por variáveis de ambiente (ver .env.example)."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
USERS_DIR = DATA_DIR / "users"
DB_PATH = DATA_DIR / "studio.db"

APP_NAME = os.getenv("APP_NAME", "AI Studio")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:3000")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
MONTAGE_FPS = 24

DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Chave mestra que cifra as API keys no banco.

    Em produção defina SECRET_KEY no ambiente: assim o banco sozinho (backup, cópia vazada)
    não revela nenhuma key. Sem a variável, uma chave é gerada em DATA_DIR/.secret_key.
    Trocar a SECRET_KEY torna as keys salvas ilegíveis (é só cadastrá-las de novo).
    """
    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env
    f = DATA_DIR / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass
        print("[aviso] SECRET_KEY não definida: chave gerada em", f, "- defina SECRET_KEY em produção.")
    return f.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()
