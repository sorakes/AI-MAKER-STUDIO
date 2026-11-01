import re

from app import config


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_pages_redirect_anonymous_users(client):
    for path in ("/", "/admin"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 307), path
        assert r.headers["location"].endswith("/login")


def test_login_page_renders_with_stable_asset_version(client):
    html1 = client.get("/login").text
    html2 = client.get("/login").text
    v1 = re.findall(r'/js/[^"]+\?v=([0-9a-f]+)', html1)
    v2 = re.findall(r'/js/[^"]+\?v=([0-9a-f]+)', html2)
    assert v1 and set(v1) == set(v2)  # hash do conteúdo: estável entre requisições
    assert len(set(v1)) == 1 and len(v1[0]) == 12


def test_static_js_is_revalidated(client):
    r = client.get("/js/i18n.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_canvas_roundtrip(make_user, login):
    from fastapi.testclient import TestClient

    from app.main import app

    make_user("canvas@test.local")
    c = login("canvas@test.local", c=TestClient(app))
    assert c.get("/api/canvas").json() == {"nodes": [], "edges": []}
    doc = {"nodes": [{"id": "n1", "type": "roteiro"}], "edges": []}
    assert c.post("/api/canvas", json=doc).json() == {"ok": True}
    assert c.get("/api/canvas").json() == doc


def test_catalog_requires_login(client):
    assert client.get("/api/catalog").status_code == 401


def test_range_request_returns_partial_content(make_user, login):
    from fastapi.testclient import TestClient

    from app.main import app

    u = make_user("range@test.local")
    f = config.USERS_DIR / str(u["id"]) / "media" / "clip.bin"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    c = login("range@test.local", c=TestClient(app))

    r = c.get(f"/files/{u['id']}/media/clip.bin", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 0-99/1024"
    assert r.headers["content-length"] == "100"
    assert r.content == (bytes(range(256)) * 4)[:100]

    r = c.get(f"/files/{u['id']}/media/clip.bin", headers={"Range": "bytes=-16"})
    assert r.status_code == 206 and len(r.content) == 16

    full = c.get(f"/files/{u['id']}/media/clip.bin")
    assert full.status_code == 200 and len(full.content) == 1024
    assert full.headers["accept-ranges"] == "bytes"
