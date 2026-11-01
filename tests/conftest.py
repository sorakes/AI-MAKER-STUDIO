"""Ambiente de teste: DATA_DIR temporário, definido antes de importar o app (config lê no import)."""
import atexit
import json
import os
import shutil
import tempfile
import time

_DATA = tempfile.mkdtemp(prefix="aistudio-test-")
atexit.register(shutil.rmtree, _DATA, ignore_errors=True)
os.environ["DATA_DIR"] = _DATA
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "admin-test-pw"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """Cliente com lifespan (init do banco, bootstrap do admin, seed)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate(client):
    """Cada teste começa sem cookie de sessão e sem contagem de tentativas de login."""
    client.cookies.clear()
    db.run("DELETE FROM login_attempts")
    yield
    client.cookies.clear()


@pytest.fixture
def make_user():
    def _make(email: str, password: str = "senha-123", role: str = "user", active: int = 1) -> dict:
        if existing := db.one("SELECT * FROM users WHERE email = ?", (email,)):
            return existing
        db.run("INSERT INTO users(email, name, password_hash, role, active, limits, created_at) VALUES(?,?,?,?,?,?,?)",
               (email, email.split("@")[0], hash_password(password), role, active,
                json.dumps({k: None for k in auth.DEFAULT_LIMITS}), time.time()))
        return db.one("SELECT * FROM users WHERE email = ?", (email,))
    return _make


@pytest.fixture
def login(client):
    def _login(email: str, password: str = "senha-123", c: TestClient | None = None) -> TestClient:
        c = c or client
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c
    return _login
