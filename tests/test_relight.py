"""Relight e estilo: ajuste local com Pillow real, prompts, endpoints com provedor simulado e run ponta a ponta."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageStat

from app import auth, jobs, registry, relight, storage, usage
from app.main import app

FAKE_MODEL = {"id": 11, "kind": "image", "label": "Modelo com refs", "provider": "openrouter", "model_id": "fake/edit",
              "caps": {"refs": True, "t2i": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.02,
              "cost_unit": "run", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}


def _uniq():
    return str(time.time_ns())


def _solid(path, color=(128, 128, 128), size=(64, 48)):
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _halves(path, size=(64, 48)):
    """Cinza médio: metade esquerda e direita iguais; usado para ver a direção da luz."""
    Image.new("RGB", size, (128, 128, 128)).save(path, format="PNG")
    return path


def _mean(img):
    return ImageStat.Stat(img.convert("RGB")).mean


@pytest.fixture
def me(make_user):
    return auth.public_user(make_user(f"relight-{_uniq()}@test.local"))


def _mine(me, name=None, color=(120, 90, 60)):
    return storage.to_url(_solid(storage.user_dir(me["id"], "uploads") / (name or f"img-{_uniq()}.png"), color))


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


# ---------- ajuste local (Pillow real) ----------

def test_golden_preset_warms_the_image():
    src = Image.new("RGB", (32, 32), (120, 120, 120))
    warm = relight.local_relight(src, "golden", 100, None)
    r, g, b = _mean(warm)
    assert r > b  # dourado: mais vermelho que azul


def test_night_preset_cools_and_darkens_the_image():
    src = Image.new("RGB", (32, 32), (150, 150, 150))
    night = relight.local_relight(src, "night", 100, None)
    r, g, b = _mean(night)
    assert b > r and sum(_mean(night)) < sum(_mean(src))


def test_intensity_zero_keeps_the_original():
    src = Image.new("RGB", (16, 16), (90, 140, 200))
    out = relight.local_relight(src, "neon", 0, None)
    assert list(out.getdata()) == list(src.getdata())


def test_light_direction_makes_the_source_side_brighter(tmp_path):
    """Luz vindo da esquerda: a metade esquerda tem de ficar mais clara que a direita."""
    src = Image.open(_halves(tmp_path / "g.png"))
    out = relight.local_relight(src, "studio", 100, [0.05, 0.5, 0.9, 0.5])
    w, h = out.size
    left = ImageStat.Stat(out.crop((0, 0, w // 2, h))).mean[0]
    right = ImageStat.Stat(out.crop((w // 2, 0, w, h))).mean[0]
    assert left > right


def test_style_transfer_moves_the_colors_toward_the_reference():
    src = Image.new("RGB", (48, 48), (60, 60, 60))
    ref = Image.new("RGB", (48, 48), (230, 120, 40))
    full = relight.local_style(src, ref, 100)
    half = relight.local_style(src, ref, 50)
    target = _mean(ref)
    dist_full = sum(abs(a - b) for a, b in zip(_mean(full), target))
    dist_src = sum(abs(a - b) for a, b in zip(_mean(src), target))
    assert dist_full < dist_src * 0.2
    assert list(relight.local_style(src, ref, 0).getdata()) == list(src.getdata())
    assert _mean(half) != _mean(full) and _mean(half) != _mean(src)


def test_apply_local_writes_the_file(tmp_path):
    src = _solid(tmp_path / "a.png", (100, 100, 100))
    ref = _solid(tmp_path / "r.png", (200, 50, 50))
    out = tmp_path / "o.png"
    relight.apply_local({"kind": "style", "strength": 80}, src, out, ref)
    with Image.open(out) as im:
        assert im.size == (64, 48)
    with pytest.raises(ValueError):
        relight.apply_local({"kind": "x"}, src, tmp_path / "x.png")


# ---------- textos e prompts ----------

def test_light_text_names_source_and_target_sides():
    t = relight.light_text([0.1, 0.5, 0.9, 0.5])
    assert "left" in t and "right" in t and "travels" in t
    assert relight.light_text(None) == ""


def test_relight_prompt_keeps_identity_and_composition():
    req = relight.RelightRequest(source_url="/files/1/a.png", preset="neon", intensity=80, light=[0.2, 0.2, 0.8, 0.8])
    p = relight.relight_prompt(req)
    assert "ORIGINAL" in p and "identity" in p and "composition" in p and "neon" in p.lower()
    assert "No drawn marks" in p and "strong (80%)" in p


def test_style_prompt_names_both_images_and_strength():
    req = relight.StyleRequest(source_url="/files/1/a.png", style_url="/files/1/b.png", strength=30)
    p = relight.style_prompt(req)
    assert "image 2" in p and "image 1" in p and "subtle strength (30%)" in p and "identity" in p


def test_light_vector_must_be_inside_the_square():
    with pytest.raises(ValueError):
        relight.RelightRequest(source_url="/f", light=[0.1, 0.2, 1.5, 0.3])


# ---------- jobs e endpoints ----------

def test_relight_job_uses_the_original_as_reference(me, ai_available):
    url = _mine(me)
    rr = jobs.RenderRequest(node_id="rl", gen="relight", prompt="p", keyframe_prompt="p", ref_image_urls=[url],
                            local_op={"kind": "relight", "preset": "golden", "intensity": 50, "light": None})
    job = jobs.prepare(me, rr)
    assert job["steps"][0]["provider"] == "openrouter"
    assert job["steps"][0]["label"] == "Relight com IA"


def test_style_job_keeps_original_first_and_style_second(me, ai_available):
    rr = jobs.RenderRequest(node_id="st", gen="style", prompt="p", keyframe_prompt="p",
                            ref_image_urls=[_mine(me), _mine(me)], local_op={"kind": "style", "strength": 70})
    job = jobs.prepare(me, rr)
    assert len(job["spec"]["ref_image_urls"]) == 2


def test_relight_runs_locally_end_to_end(me, no_ai):
    url = _mine(me, color=(100, 100, 100))
    rr = jobs.RenderRequest(node_id="rl-local", gen="relight", prompt="p", keyframe_prompt="p", ref_image_urls=[url],
                            local_op={"kind": "relight", "preset": "night", "intensity": 100, "light": None})
    job = jobs.prepare(me, rr)
    assert job["steps"][0]["provider"] == "local"
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    with Image.open(storage.resolve(job["output"], me)) as im:
        assert im.size == (64, 48)
        assert _mean(im)[2] > _mean(im)[0]  # noite: azulado


def test_style_runs_locally_end_to_end(me, no_ai):
    src = _mine(me, color=(70, 70, 70))
    ref = _mine(me, color=(220, 60, 60))
    rr = jobs.RenderRequest(node_id="st-local", gen="style", prompt="p", keyframe_prompt="p",
                            ref_image_urls=[src, ref], local_op={"kind": "style", "strength": 100})
    job = jobs.prepare(me, rr)
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    with Image.open(storage.resolve(job["output"], me)) as im:
        assert _mean(im)[0] > _mean(im)[2]  # ficou avermelhada como a referência


def _client(login, make_user):
    u = make_user(f"relight-api-{_uniq()}@test.local")
    return login(u["email"], c=TestClient(app)), u


def test_relight_endpoint_ai_and_local_modes(login, make_user, ai_available, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    url = _mine(auth.public_user(u))
    r = c.post("/api/image/relight", json={"source_url": url, "preset": "backlight", "intensity": 70,
                                           "light": [0.1, 0.1, 0.9, 0.9]})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "ai"
    spec = started[0]["spec"]
    assert spec["gen"] == "relight" and spec["ref_image_urls"] == [url]
    assert spec["local_op"]["preset"] == "backlight" and spec["local_op"]["light"] == [0.1, 0.1, 0.9, 0.9]
    assert "strong backlight" in spec["prompt"]


def test_style_endpoint_passes_both_images(login, make_user, ai_available, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    me = auth.public_user(u)
    a, b = _mine(me), _mine(me, color=(20, 20, 200))
    r = c.post("/api/image/style-transfer", json={"source_url": a, "style_url": b, "strength": 40})
    assert r.status_code == 200, r.text
    assert started[0]["spec"]["ref_image_urls"] == [a, b]
    assert r.json()["mode"] == "ai"


def test_no_model_falls_back_to_local_and_says_so(login, make_user, no_ai, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    me = auth.public_user(u)
    r = c.post("/api/image/style-transfer", json={"source_url": _mine(me), "style_url": _mine(me), "strength": 50})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    assert started[0]["steps"][0]["label"] == "Estilo simples"


@pytest.mark.parametrize("payload", [
    {"preset": "rainbow"}, {"intensity": 101}, {"light": [0.1, 0.2, 0.3]}, {"light": [0.1, 0.2, 1.4, 0.3]},
])
def test_relight_rejects_invalid_input(login, make_user, no_ai, payload):
    c, u = _client(login, make_user)
    body = {"source_url": _mine(auth.public_user(u)), **payload}
    assert c.post("/api/image/relight", json=body).status_code == 422


def test_source_must_belong_to_the_user(login, make_user, no_ai):
    c, _ = _client(login, make_user)
    other = auth.public_user(make_user(f"outro-rl-{_uniq()}@test.local"))
    assert c.post("/api/image/relight", json={"source_url": _mine(other)}).status_code == 403
    assert c.post("/api/image/style-transfer", json={"source_url": _mine(other), "style_url": _mine(other)}).status_code == 403
