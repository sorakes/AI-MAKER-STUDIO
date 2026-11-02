"""Rotas async que chamam código síncrono (SQLite/IO/render) devem rodar fora da thread do event loop."""
import asyncio

from fastapi.testclient import TestClient

from app import assets, sprites
from app.main import app


def _on_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _admin_client(login):
    return login("admin@test.local", "admin-test-pw", c=TestClient(app))


def test_generate_asset_runs_off_event_loop(login, monkeypatch):
    c = _admin_client(login)
    asset = c.post("/api/assets", json={"kind": "character", "name": "Teste"}).json()
    seen = {}

    def fake_generate(asset_id, user, body):
        seen["on_loop"] = _on_loop_thread()
        return {"jobs": []}

    monkeypatch.setattr(assets, "generate", fake_generate)
    r = c.post(f"/api/assets/{asset['id']}/generate", json={})
    assert r.status_code == 200, r.text
    assert seen == {"on_loop": False}


def test_pose_render_runs_off_event_loop(login, monkeypatch):
    c = _admin_client(login)
    asset = c.post("/api/assets", json={"kind": "character", "name": "Pose"}).json()
    seen = {}

    def fake_render(user, a, body):
        seen["on_loop"] = _on_loop_thread()
        return {"job": {"id": "job-x", "user_id": user["id"], "created": 0, "status": "queued", "steps": [],
                        "spec": {}, "progress": 0, "detail": None, "error": None, "est_cost": 0,
                        "output": None, "media": None, "keyframe": None}}

    monkeypatch.setattr(sprites, "render_pose", fake_render)
    r = c.post(f"/api/assets/{asset['id']}/pose-render", json={"key": "front"})
    assert r.status_code == 200, r.text
    assert seen == {"on_loop": False}
