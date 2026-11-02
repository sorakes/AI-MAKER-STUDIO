"""Regerar imagem por desenho: endpoint com visão e provedor simulados; as marcações nunca vão ao gerador."""
import json

import pytest
from fastapi.testclient import TestClient

from app import direction, jobs, registry, sketch, storage, usage
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
ANNOTATED = "data:image/jpeg;base64,QU5OT1RBVEVEX0ZSQU1FX1NFTlRfVE9fVklTSU9O"

FAKE_MODEL = {"id": 7, "kind": "image", "label": "Modelo teste", "provider": "openrouter", "model_id": "fake/img",
              "caps": {"refs": True, "t2i": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.01,
              "cost_unit": "run", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}


@pytest.fixture
def env(monkeypatch, make_user, login):
    """Usuário logado, imagem de origem no disco, modelo com referências e render sem execução real."""
    u = make_user("sketch@test.local")
    c = login("sketch@test.local", c=TestClient(app))
    src = storage.user_dir(u["id"], "renders") / "origem.png"
    from PIL import Image
    Image.new("RGB", (64, 48), (120, 60, 200)).save(src, format="PNG")
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    monkeypatch.setattr(registry, "pick", lambda user, kind, model_id=None, need=None: FAKE_MODEL if kind == "image" else None)
    monkeypatch.setattr(registry, "get", lambda mid: FAKE_MODEL)
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [FAKE_MODEL])
    monkeypatch.setattr(registry, "estimate", lambda m, seconds=5: 0.01)
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)
    monkeypatch.setattr(sketch.llm, "source", lambda: "teste")
    return {"client": c, "user": u, "source_url": storage.to_url(src), "started": started, "monkeypatch": monkeypatch}


def _body(source_url, layers=None, annotated=ANNOTATED):
    layers = layers if layers is not None else [
        {"color": "red", "label": "VERMELHO", "prompt": "troque a bola vermelha por uma azul",
         "shapes": [{"type": "loop", "points": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]]}]},
        {"color": "yellow", "label": "AMARELO", "prompt": "a sombra fica mais suave",
         "shapes": [{"type": "path", "points": [[0.1, 0.1], [0.3, 0.3]]}]},
    ]
    return {"node_id": "shot-1", "name": "Produto", "source_url": source_url, "annotated": annotated,
            "layers": layers, "intent": "garrafa no balcão", "aspect": "1:1", "ref_image_urls": []}


def _vision(monkeypatch, payload):
    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps(payload) if payload is not None else None

    monkeypatch.setattr(sketch.llm, "chat", chat)
    return calls


def test_happy_path_sends_original_as_reference_and_never_the_annotated_image(env):
    calls = _vision(env["monkeypatch"], {"edit_en": "Replace the red ball with a blue one.",
                                         "keep_en": "the bottle and the table", "notes_pt": []})
    r = env["client"].post("/api/image/regenerate-from-sketch", json=_body(env["source_url"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edit"] == {"prompt_en": "Replace the red ball with a blue one.", "notes_pt": [], "source": "teste"}

    job = env["started"][0]
    spec = job["spec"]
    assert spec["gen"] == "image" and spec["require_refs"] is True
    assert spec["ref_image_urls"] == [env["source_url"]]  # a original é a referência
    assert "QU5OT1RBVEVEX0ZSQU1FX1NFTlRfVE9fVklTSU9O" not in json.dumps(spec)  # o desenho não vai ao gerador
    assert "Replace the red ball with a blue one." in spec["prompt"]
    assert "NO drawn marks" in spec["prompt"]
    # a visão recebeu a imagem anotada (e só ela recebe o desenho)
    user_content = calls[0][0][1]["content"]
    assert user_content[1]["image_url"]["url"].startswith("data:image/")


def test_without_vision_the_rules_build_the_edit_from_colors_and_marks(env):
    _vision(env["monkeypatch"], None)
    r = env["client"].post("/api/image/regenerate-from-sketch", json=_body(env["source_url"]))
    assert r.status_code == 200, r.text
    assert r.json()["edit"]["source"] == "regras"
    assert "troque a bola vermelha por uma azul" in r.json()["edit"]["prompt_en"]
    assert "hollow ring = end" in r.json()["edit"]["prompt_en"]  # o anel vazio marca o destino do elemento


def test_empty_drawing_is_rejected(env):
    _vision(env["monkeypatch"], None)
    empty = [{"color": "red", "label": "VERMELHO", "prompt": "   ", "shapes": []}]
    r = env["client"].post("/api/image/regenerate-from-sketch", json=_body(env["source_url"], layers=empty))
    assert r.status_code == 400
    assert "marcação" in r.json()["detail"]


def test_annotated_must_be_an_image_data_url(env):
    _vision(env["monkeypatch"], None)
    r = env["client"].post("/api/image/regenerate-from-sketch",
                           json=_body(env["source_url"], annotated="http://evil.example/x.jpg"))
    assert r.status_code == 400


def test_source_image_must_belong_to_the_user(env, make_user, login):
    make_user("outro-sketch@test.local")
    outro = login("outro-sketch@test.local", c=TestClient(app))
    _vision(env["monkeypatch"], None)
    r = outro.post("/api/image/regenerate-from-sketch", json=_body(env["source_url"]))
    assert r.status_code == 403
    assert env["started"] == []


def test_no_reference_capable_model_fails_instead_of_losing_the_scene(env):
    env["monkeypatch"].setattr(registry, "pick", lambda *a, **k: None)
    env["monkeypatch"].setattr(registry, "for_user", lambda *a, **k: [])
    _vision(env["monkeypatch"], {"edit_en": "x", "keep_en": "", "notes_pt": []})
    r = env["client"].post("/api/image/regenerate-from-sketch", json=_body(env["source_url"]))
    assert r.status_code == 400
    assert "referências" in r.json()["detail"]
    assert env["started"] == []


def test_edit_prompt_keeps_the_original_as_image_one_and_forbids_marks():
    p = sketch.edit_prompt("Make the sky orange.", "the people")
    assert "image 1" in p and "ORIGINAL" in p and "Make the sky orange." in p and "the people" in p
    assert "NO drawn marks" in p and "lines, arrows, circles" in p


def test_build_request_only_references_the_source_and_node_refs():
    req = sketch.SketchRequest(node_id="n", source_url="/files/1/renders/a.png", annotated=ANNOTATED,
                               ref_image_urls=["/files/1/refs/logo.png"],
                               layers=[direction.Layer(color="red", prompt="x", shapes=[])])
    rr = sketch.build_request(req, {"edit_en": "Edit it.", "keep_en": ""})
    assert rr.ref_image_urls == ["/files/1/renders/a.png", "/files/1/refs/logo.png"]
    assert rr.require_refs and rr.gen == "image"
    assert ANNOTATED not in rr.model_dump_json()


def test_vision_prompt_says_region_and_element_marks_without_arrows():
    s = sketch.SYSTEM
    assert "closed loop circles a REGION" in s
    assert "solid DOT" in s and "hollow RING" in s
    assert "never ask for lines, arrows, circles" in s
