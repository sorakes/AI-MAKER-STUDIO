"""Ambiente de teste: DATA_DIR temporário, definido antes de importar o app (config lê no import)."""
import asyncio
import atexit
import json
import os
import traceback
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


SYNC_DB_FUNCS = ("one", "all_", "run", "tx", "get_setting", "set_setting")


@pytest.fixture(autouse=True)
def _no_sync_sql_on_event_loop(monkeypatch):
    """Guarda de runtime: SQL síncrono (db.one/all_/run/tx) chamado com um event loop rodando na thread
    é violação (travaria o servidor). Falha o teste que causou a chamada."""
    violations = []

    def guard(name, fn):
        def wrapper(*a, **k):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return fn(*a, **k)
            where = [f"{f.filename.split('app')[-1]}:{f.lineno} {f.name}" for f in traceback.extract_stack()
                     if f"{os.sep}app{os.sep}" in f.filename]
            violations.append(f"{name} @ {where[-1] if where else '?'}")
            return fn(*a, **k)
        return wrapper

    for n in SYNC_DB_FUNCS:
        monkeypatch.setattr(db, n, guard(n, getattr(db, n)))
    yield
    assert violations == [], f"SQL síncrono no event loop: {sorted(set(violations))}"
