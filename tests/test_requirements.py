"""Painel admin: modelos obrigatórios por recurso (nó) e se cada um está coberto."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import performance, providers, registry, requirements, tunnel, videoedit
from app.main import app


def _m(mid, kind="video", label=None, caps=None, available=True):
    return {"id": hash(mid) % 10000, "model_id": mid, "kind": kind, "label": label or mid, "caps": caps or {},
            "available": available}


@pytest.fixture
def env(monkeypatch):
    state = {"key": True, "tunnel": True, "models": []}
    monkeypatch.setattr(providers, "available", lambda pid, key_id=None: state["key"])
    monkeypatch.setattr(tunnel, "available", lambda: state["tunnel"])
    monkeypatch.setattr(registry, "list_models", lambda kind=None: state["models"])
    return state


def _by_key(rows):
    return {r["key"]: r for r in rows}


def test_every_node_with_requirements_is_listed(env):
    keys = set(_by_key(requirements.report()))
    assert {"videoedit", "speech", "music", "sfx", "subtitles", "performance", "lipsync", "upscale", "outpaint",
            "relight"} <= keys


def test_video_edit_needs_key_and_tunnel_and_lists_only_video_input_models(env):
    env["models"] = [_m("bytedance/seedance-2.0-fast", label="Seedance 2.0 Fast"), _m("google/veo-3.1", label="Veo 3.1")]
    r = _by_key(requirements.report())["videoedit"]
    assert r["status"] == "ok" and "FLUX Video Edit" in r["using"] and "Wan 2.7" in r["using"]
    assert "Seedance 2.0 Fast" in r["using"] and "Veo" not in r["using"]  # só quem recebe vídeo
    env["tunnel"] = False
    r = _by_key(requirements.report())["videoedit"]
    assert r["status"] == "missing" and "túnel" in r["fix"]
    env["key"] = False
    r = _by_key(requirements.report())["videoedit"]
    assert "key do OpenRouter" in r["fix"] and "túnel" in r["fix"]


def test_fixed_models_only_need_the_openrouter_key(env):
    rows = _by_key(requirements.report())
    assert rows["speech"]["status"] == "ok" and rows["music"]["status"] == "ok" and rows["subtitles"]["status"] == "ok"
    assert rows["sfx"]["status"] == "local"
    env["key"] = False
    rows = _by_key(requirements.report())
    assert all(rows[k]["status"] == "missing" for k in ("speech", "music", "subtitles"))
    assert rows["sfx"]["status"] == "local"  # efeito é ffmpeg: nunca depende de key


def test_performance_needs_its_specific_model(env):
    assert _by_key(requirements.report())["performance"]["status"] == "missing"
    env["models"] = [_m(performance.PERF_MODEL_ID, label="Seedance 2.0 Fast")]
    assert _by_key(requirements.report())["performance"]["status"] == "ok"
    env["models"] = [_m(performance.PERF_MODEL_ID, available=False)]  # sem key: não conta
    assert _by_key(requirements.report())["performance"]["status"] == "missing"


def test_image_tools_fall_back_to_the_simple_version(env):
    rows = _by_key(requirements.report())
    assert rows["upscale"]["status"] == "fallback" and rows["lipsync"]["status"] == "fallback"
    env["models"] = [_m("google/nano", kind="image", label="Nano Banana", caps={"refs": True}),
                     _m("x/lips", label="Lips", caps={"lipsync": True})]
    rows = _by_key(requirements.report())
    assert rows["upscale"]["status"] == "ok" and "Nano Banana" in rows["upscale"]["using"]
    assert rows["lipsync"]["status"] == "ok"


def test_endpoint_is_admin_only(login, make_user):
    c = TestClient(app)
    assert c.get("/api/admin/requirements").status_code in (401, 403)
    u = make_user("req-user@test.local")
    login(u["email"], c=c)
    assert c.get("/api/admin/requirements").status_code in (401, 403)
    a = TestClient(app)
    login("admin@test.local", "admin-test-pw", c=a)
    r = a.get("/api/admin/requirements")
    assert r.status_code == 200 and any(x["key"] == "videoedit" for x in r.json())


def test_admin_panel_shows_the_block():
    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    assert "api('/api/admin/requirements')" in js and "Modelos obrigatórios por recurso" in js
    assert "${renderRequirements()}" in js
