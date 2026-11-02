"""Upscale criativo: fallback Pillow real, caminho de IA com provedor simulado e validações do endpoint."""
import asyncio
import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth, jobs, registry, storage, upscale, usage
from app.main import app

FAKE_MODEL = {"id": 9, "kind": "image", "label": "Modelo com refs", "provider": "openrouter", "model_id": "fake/edit",
              "caps": {"refs": True, "t2i": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.02,
              "cost_unit": "run", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}


def _png(path, size=(64, 48)):
    """Imagem de teste com metade clara e metade escura (borda nítida para a nitidez ter o que realçar)."""
    img = Image.new("RGB", size, (60, 40, 120))
    img.paste((240, 240, 240), (0, 0, size[0] // 2, size[1]))
    img.save(path, format="PNG")
    return path


def _uniq():
    return str(time.time_ns())


@pytest.fixture
def me(make_user):
    """Usuário como o app enxerga (limites já decodificados), para chamar jobs.prepare/_run direto."""
    return auth.public_user(make_user(f"upscale-{_uniq()}@test.local"))


@pytest.fixture
def ai_available(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda user, kind, model_id=None, need=None: FAKE_MODEL if (need or {}).get("refs") else None)
    monkeypatch.setattr(registry, "get", lambda mid: FAKE_MODEL)

    async def aget(mid):
        return FAKE_MODEL
    monkeypatch.setattr(registry, "aget", aget)
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [FAKE_MODEL])
    monkeypatch.setattr(registry, "estimate", lambda m, seconds=5: 0.02)
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda *a, **k: None)
    monkeypatch.setattr(registry, "for_user", lambda *a, **k: [])
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _mine(me, name=None):
    """Arquivo de entrada na pasta do usuário; devolve a URL /files/..."""
    p = _png(storage.user_dir(me["id"], "uploads") / (name or f"entrada-{_uniq()}.png"))
    return storage.to_url(p)


# ---------- Pillow (sem simulação) ----------

@pytest.mark.parametrize("factor", [2, 4])
def test_local_upscale_produces_exact_size(tmp_path, factor):
    src = _png(tmp_path / "a.png", size=(64, 48))
    out = upscale.resize_file(src, factor, tmp_path / f"b{factor}.png")
    with Image.open(out) as im:
        assert im.size == (64 * factor, 48 * factor)
        assert im.mode == "RGB"


def test_local_upscale_sharpens_beyond_plain_lanczos(tmp_path):
    src = _png(tmp_path / "a.png", size=(32, 32))
    out = upscale.resize_file(src, 2, tmp_path / "s.png")
    with Image.open(src) as base:
        plain = list(base.resize((64, 64), Image.Resampling.LANCZOS).convert("L").getdata())
    with Image.open(out) as sharp:
        sharpened = list(sharp.convert("L").getdata())
    assert sharpened != plain  # a nitidez leve altera a imagem em relação ao Lanczos puro


def test_local_upscale_in_place_and_invalid_factor(tmp_path):
    src = _png(tmp_path / "x.png", size=(20, 10))
    upscale.resize_file(src, 2)  # sobrescreve o próprio arquivo
    with Image.open(src) as im:
        assert im.size == (40, 20)
    with pytest.raises(ValueError):
        upscale.resize_file(src, 3)


def test_prompt_reflects_factor_size_and_sliders():
    req = upscale.UpscaleRequest(source_url="/files/1/x.png", factor=4, creativity=90, similarity=20,
                                 prompt="mantenha o rótulo")
    p = upscale.prompt_for(req, 100, 50).lower()
    assert "400x200" in p and "reimagine freely" in p and "add rich" in p and "mantenha o rótulo" in p
    assert "no marks" in p


# ---------- jobs: caminho de IA e fallback local ----------

def test_ai_plan_when_a_reference_model_exists(me, ai_available):
    rr = jobs.RenderRequest(node_id="up", gen="upscale", prompt="p", keyframe_prompt="p",
                            ref_image_urls=[_mine(me)], upscale_factor=2)
    job = jobs.prepare(me, rr)
    assert job["steps"][0]["provider"] == "openrouter"
    assert job["steps"][0]["label"] == "Upscale criativo"


def test_local_plan_when_no_reference_model(me, no_ai):
    rr = jobs.RenderRequest(node_id="up", gen="upscale", prompt="p", keyframe_prompt="p",
                            ref_image_urls=[_mine(me)], upscale_factor=4)
    job = jobs.prepare(me, rr)
    assert job["steps"][0]["provider"] == "local"
    assert job["steps"][0]["label"] == "Upscale simples"
    assert job["est_cost"] == 0


def test_ai_output_is_brought_to_the_exact_factor(me, ai_available, monkeypatch):
    """O provedor devolve 300x200; com fator 4 o resultado final tem de ser 1200x800."""
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 200, 10)).save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    monkeypatch.setattr(jobs, "_openrouter_image", lambda m, prompt, spec, refs: (data_url, 0.02))
    rr = jobs.RenderRequest(node_id="up-ai", gen="upscale", prompt="p", keyframe_prompt="p",
                            ref_image_urls=[_mine(me)], upscale_factor=4)
    job = jobs.prepare(me, rr)
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    with Image.open(storage.resolve(job["output"], me)) as im:
        assert im.size == (1200, 800)


def test_local_job_runs_end_to_end_with_pillow(me, no_ai):
    rr = jobs.RenderRequest(node_id="up-local", gen="upscale", prompt="x", keyframe_prompt="x",
                            ref_image_urls=[_mine(me)], upscale_factor=2)
    job = jobs.prepare(me, rr)
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    assert job["steps"][0]["cost"] == 0.0
    with Image.open(storage.resolve(job["output"], me)) as im:
        assert im.size == (128, 96)


# ---------- endpoint ----------

def _client(login, make_user):
    u = make_user(f"upscale-api-{_uniq()}@test.local")
    return login(u["email"], c=TestClient(app)), u


def test_endpoint_ai_path_references_the_original(login, make_user, ai_available, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    url = _mine(auth.public_user(u))
    r = c.post("/api/image/upscale", json={"source_url": url, "factor": 2, "creativity": 80, "similarity": 60,
                                           "node_id": "up-1"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "ai"
    spec = started[0]["spec"]
    assert spec["gen"] == "upscale" and spec["upscale_factor"] == 2
    assert spec["ref_image_urls"] == [url]
    assert "128x96" in spec["keyframe_prompt"]


def test_endpoint_without_ai_is_local_and_says_so(login, make_user, no_ai, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    r = c.post("/api/image/upscale", json={"source_url": _mine(auth.public_user(u)), "factor": 4})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    assert started[0]["steps"][0]["provider"] == "local"


@pytest.mark.parametrize("payload", [
    {"factor": 3}, {"factor": 2, "creativity": 101}, {"factor": 2, "similarity": -1}, {"factor": 2, "prompt": "x" * 1001},
])
def test_invalid_parameters_are_rejected(login, make_user, payload, no_ai):
    c, u = _client(login, make_user)
    r = c.post("/api/image/upscale", json={"source_url": _mine(auth.public_user(u)), **payload})
    assert r.status_code == 422


def test_source_must_belong_to_the_user(login, make_user, no_ai):
    c, _ = _client(login, make_user)
    other = auth.public_user(make_user(f"outro-{_uniq()}@test.local"))
    r = c.post("/api/image/upscale", json={"source_url": _mine(other), "factor": 2})
    assert r.status_code == 403


def test_non_image_source_is_rejected(login, make_user, no_ai):
    c, u = _client(login, make_user)
    txt = storage.user_dir(u["id"], "uploads") / "nao-imagem.png"
    txt.write_bytes(b"isto nao e uma imagem")
    r = c.post("/api/image/upscale", json={"source_url": storage.to_url(txt), "factor": 2})
    assert r.status_code == 400


def test_endpoint_ai_output_keeps_source_aspect_and_exact_size(me, ai_available, monkeypatch, login, make_user):
    """Regressão: a IA devolve outro aspecto (1376x768); o resultado tem de ser exatamente original × fator."""
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    buf = io.BytesIO()
    Image.new("RGB", (1376, 768), (10, 200, 10)).save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    monkeypatch.setattr(jobs, "_openrouter_image", lambda m, prompt, spec, refs: (data_url, 0.02))
    u = make_user(f"upscale-size-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    src = _png(storage.user_dir(me2["id"], "uploads") / "quadrada.png", size=(64, 64))
    r = c.post("/api/image/upscale", json={"source_url": storage.to_url(src), "factor": 2, "creativity": 10, "similarity": 90})
    assert r.status_code == 200, r.text
    assert started[0]["spec"]["target_size"] == [128, 128]
    asyncio.run(jobs._run(started[0], me2))
    assert started[0]["status"] == "done", started[0].get("error")
    with Image.open(storage.resolve(started[0]["output"], me2)) as out:
        assert out.size == (128, 128)
