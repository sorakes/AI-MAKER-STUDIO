"""Storyboard: validação e limites do plano (LLM simulado), estimativa de custo e endpoint."""
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import camera, llm, storyboard
from app.main import app


def _uniq():
    return str(time.time_ns())


def _shot(**kw):
    base = {"title": "Gancho", "description": "garrafa gelada em destaque", "image_prompt": "cold bottle, studio light",
            "video_prompt": "slow push in", "duration": 4, "camera": "push_in", "refs": []}
    return {**base, **kw}


def _req(**kw):
    base = {"brief": "comercial de verão com uma garrafa gelada e amigos", "shots": 3, "total": 12}
    return storyboard.PlanRequest(**{**base, **kw})


def test_plan_keeps_requested_shot_count_and_maps_camera_presets(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"title": "Verão", "shots": [_shot(), _shot(camera="bogus"), _shot(camera="orbit")]})
    out = storyboard.build_plan(_req())
    assert out["title"] == "Verão" and len(out["shots"]) == 3
    assert [s["camera"] for s in out["shots"]] == ["push_in", "static", "orbit"]  # id inválido cai em travada
    assert out["shots"][0]["camera_hint"] == camera.MOVES["push_in"]["prompt"]  # texto do preset do app/camera.py


def test_plan_trims_extra_shots_and_drops_incomplete_ones(monkeypatch):
    items = [_shot(), {"title": "", "description": "x", "image_prompt": "y", "video_prompt": "z"}, _shot(), _shot(), _shot()]
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"shots": items})
    out = storyboard.build_plan(_req(shots=3))
    assert len(out["shots"]) == 2  # 3 pedidos, 1 incompleto descartado -> 2 válidos (o mínimo aceito)


def test_durations_are_clamped_and_scaled_to_the_requested_total(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"shots": [_shot(duration=30), _shot(duration=0.5), _shot(duration=4)]})
    out = storyboard.build_plan(_req(total=12))
    durations = [s["duration"] for s in out["shots"]]
    assert all(storyboard.MIN_SHOT_S <= d <= storyboard.MAX_SHOT_S for d in durations)
    assert abs(sum(durations) - 12) <= 2.5  # perto do total (os limites de 2 a 8 s podem empurrar)


def test_refs_match_saved_inventory_by_name_and_keep_new_names(monkeypatch):
    refs = ["ana", "Garrafa Azul", "Moto Nova"]
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"shots": [_shot(refs=refs), _shot()]})
    req = _req(assets=[storyboard.Asset(name="Ana", kind="personagem"), storyboard.Asset(name="Garrafa azul", kind="produto")])
    out = storyboard.build_plan(req)
    assert out["shots"][0]["refs"] == ["Ana", "Garrafa azul", "Moto Nova"]  # inventário com grafia salva; novo fica como está


def test_offline_model_or_bad_answer_is_an_error(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: None)
    with pytest.raises(storyboard.StoryboardError, match="Sem modelo"):
        storyboard.build_plan(_req())
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"shots": [_shot()]})  # só 1 plano válido
    with pytest.raises(storyboard.StoryboardError, match="incompleto"):
        storyboard.build_plan(_req())


def test_estimate_is_images_plus_video_seconds():
    shots = [{"duration": 4.0}] * 4
    est = storyboard.estimate(shots)
    assert est["images"] == 4 and est["video_seconds"] == 16.0
    assert est["usd"] == round(4 * 0.034 + 16 * 0.09, 2)
    assert est["image_usd"] == storyboard.IMAGE_USD and est["video_usd_per_s"] == storyboard.VIDEO_USD_PER_S


def test_request_limits_are_enforced():
    with pytest.raises(ValidationError):
        _req(shots=2)  # mínimo 3
    with pytest.raises(ValidationError):
        _req(shots=7)  # máximo 6
    with pytest.raises(ValidationError):
        _req(total=61)
    with pytest.raises(ValidationError):
        _req(aspect="4:3")
    with pytest.raises(ValidationError):
        _req(brief="curto")
    with pytest.raises(ValidationError):
        _req(assets=[{"name": "x", "kind": "logo"}])


def test_endpoint_needs_login_and_returns_plan(login, make_user, monkeypatch):
    body = {"brief": "comercial de verão com uma garrafa gelada", "shots": 3, "total": 12}
    anon = TestClient(app).post("/api/storyboard/plan", json=body)
    assert anon.status_code == 401
    u = make_user(f"sb-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"title": "T", "shots": [_shot(), _shot(), _shot()]})
    r = c.post("/api/storyboard/plan", json=body)
    assert r.status_code == 200, r.text
    assert len(r.json()["shots"]) == 3 and r.json()["estimate"]["images"] == 3


def test_endpoint_validates_and_reports_offline_model(login, make_user, monkeypatch):
    u = make_user(f"sb-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    assert c.post("/api/storyboard/plan", json={"brief": "curto", "shots": 3, "total": 12}).status_code == 422
    assert c.post("/api/storyboard/plan", json={"brief": "comercial de verão com uma garrafa", "shots": 9, "total": 12}).status_code == 422
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: None)
    r = c.post("/api/storyboard/plan", json={"brief": "comercial de verão com uma garrafa", "shots": 3, "total": 12})
    assert r.status_code == 503 and "Sem modelo" in r.json()["detail"]
