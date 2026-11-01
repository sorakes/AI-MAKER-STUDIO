from fastapi.testclient import TestClient

from app import config
from app.main import app


def _anon():
    return TestClient(app)  # sem `with`: não roda o lifespan de novo


def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401


def test_wrong_password_is_rejected(client, make_user):
    make_user("errado@test.local")
    r = client.post("/api/auth/login", json={"email": "errado@test.local", "password": "outra"})
    assert r.status_code == 401


def test_login_sets_httponly_cookie_and_me_works(make_user):
    make_user("ana@test.local")
    c = _anon()
    r = c.post("/api/auth/login", json={"email": "ana@test.local", "password": "senha-123"})
    assert r.status_code == 200
    assert "studio_session=" in r.headers["set-cookie"]
    assert "httponly" in r.headers["set-cookie"].lower()
    me = c.get("/api/me").json()
    assert me["email"] == "ana@test.local"
    assert me["is_admin"] is False


def test_logout_invalidates_session(make_user):
    make_user("logout@test.local")
    c = _anon()
    c.post("/api/auth/login", json={"email": "logout@test.local", "password": "senha-123"})
    assert c.get("/api/me").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401


def test_inactive_user_cannot_log_in(client, make_user):
    make_user("inativo@test.local", active=0)
    r = client.post("/api/auth/login", json={"email": "inativo@test.local", "password": "senha-123"})
    assert r.status_code == 403


def test_admin_bootstrap_uses_env_password():
    c = _anon()
    r = c.post("/api/auth/login", json={"email": "admin@test.local", "password": "admin-test-pw"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert c.get("/api/me").json()["is_admin"] is True


def test_regular_user_cannot_open_admin_api(make_user, login):
    make_user("comum@test.local")
    c = login("comum@test.local", c=_anon())
    assert c.get("/api/admin/overview").status_code == 403


def test_user_cannot_read_another_users_file(make_user, login):
    dono = make_user("dono@test.local")
    make_user("intruso@test.local")
    f = config.USERS_DIR / str(dono["id"]) / "privado.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("segredo")
    c = login("intruso@test.local", c=_anon())
    assert c.get(f"/files/{dono['id']}/privado.txt").status_code == 403
    assert login("dono@test.local", c=_anon()).get(f"/files/{dono['id']}/privado.txt").status_code == 200
