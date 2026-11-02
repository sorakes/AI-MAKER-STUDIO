"""Expandir (outpaint) e editar região (inpaint): geometria, Pillow real, recomposição e endpoints com provedor simulado."""
import asyncio
import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth, jobs, outpaint, registry, storage, usage
from app.main import app

FAKE_MODEL = {"id": 13, "kind": "image", "label": "Modelo com refs", "provider": "openrouter", "model_id": "fake/edit",
              "caps": {"refs": True, "t2i": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.02,
              "cost_unit": "run", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}
GREEN = (30, 220, 60)


def _uniq():
    return str(time.time_ns())


def _solid(path, color, size=(64, 48)):
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _rect_mask(size, box):
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def _ai_png(color, size):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def me(make_user):
    return auth.public_user(make_user(f"outpaint-{_uniq()}@test.local"))


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


def _mine(me, color=(30, 120, 200)):
    return storage.to_url(_solid(storage.user_dir(me["id"], "uploads") / f"src-{_uniq()}.png", color))


# ---------- geometria ----------

@pytest.mark.parametrize("aspect", ["1:1", "16:9", "9:16", "4:5", "21:9"])
def test_layout_hits_the_aspect_and_contains_the_original(aspect):
    W, H, x, y = outpaint.layout(64, 48, aspect, "center")
    tw, th = outpaint.ASPECTS[aspect]
    assert abs(W / H - tw / th) < 0.05
    assert W >= 64 and H >= 48 and 0 <= x <= W - 64 and 0 <= y <= H - 48


@pytest.mark.parametrize("anchor,expect", [("left", (0, None)), ("right", (None, 0)), ("top", (None, 0)), ("bottom", (None, None))])
def test_anchor_places_the_original_on_the_chosen_side(anchor, expect):
    W, H, x, y = outpaint.layout(64, 48, "16:9", anchor)
    if anchor == "left":
        assert x == 0
    if anchor == "right":
        assert x == W - 64
    if anchor == "top":
        assert y == 0
    if anchor == "bottom":
        assert y == H - 48


# ---------- Pillow real ----------

def test_local_outpaint_keeps_the_original_pixels_exactly():
    src = Image.new("RGB", (64, 48), (200, 30, 30))
    W, H, x, y = outpaint.layout(64, 48, "21:9", "center")
    out = outpaint.local_outpaint(src, W, H, x, y)
    assert out.size == (W, H)
    assert out.crop((x, y, x + 64, y + 48)).tobytes() == src.tobytes()
    assert out.getpixel((0, 0)) != (128, 128, 128)  # a área nova foi preenchida, não ficou cinza


def test_canvas_and_keep_mask_mark_the_new_area():
    src = Image.new("RGB", (32, 32), (10, 10, 240))
    W, H, x, y = outpaint.layout(32, 32, "16:9", "left")
    canvas = outpaint.canvas_image(src, W, H, x, y)
    keep = outpaint.keep_for_outpaint(W, H, x, y, 32, 32)
    assert canvas.getpixel((W - 1, 0)) == outpaint.FILL
    assert keep.getpixel((x + 1, 1)) == 255 and keep.getpixel((W - 1, 0)) == 0


def test_marked_image_paints_only_the_mask_in_magenta():
    src = Image.new("RGB", (20, 20), (50, 50, 50))
    mask = _rect_mask((20, 20), (5, 5, 10, 10))
    marked = outpaint.marked_image(src, mask)
    assert marked.getpixel((6, 6)) == outpaint.MARK
    assert marked.getpixel((0, 0)) == (50, 50, 50)


def test_local_inpaint_changes_only_inside_the_mask():
    src = Image.new("RGB", (40, 40), (200, 30, 30))
    src.paste((30, 30, 230), (20, 0, 40, 40))  # metade direita azul: a região pintada fica na esquerda
    mask = _rect_mask((40, 40), (4, 4, 14, 14))
    out = outpaint.local_inpaint(src, mask)
    assert out.getpixel((30, 30)) == (30, 30, 230)          # fora da máscara: idêntico
    assert out.getpixel((0, 0)) == (200, 30, 30)
    assert out.getpixel((8, 8)) != (200, 30, 30)            # dentro: preenchido pela média ao redor


def test_finish_ai_puts_the_original_back_where_the_mask_protects(tmp_path):
    original = Image.new("RGB", (40, 40), (5, 6, 7))
    original_path = tmp_path / "o.png"
    original.save(original_path)
    ai = tmp_path / "ai.png"
    Image.new("RGB", (300, 200), GREEN).save(ai)
    keep = _rect_mask((40, 40), (0, 0, 40, 20))  # branco = fica o original (metade de cima)
    keep_path = tmp_path / "k.png"
    keep.save(keep_path)
    dest = tmp_path / "out.png"
    outpaint.finish_ai(ai, original_path, keep_path, (40, 40), dest)
    with Image.open(dest) as out:
        assert out.size == (40, 40)
        assert out.getpixel((10, 5)) == (5, 6, 7)   # protegido: original exato
        assert out.getpixel((10, 30)) == GREEN       # não protegido: saída da IA


def test_apply_local_writes_the_right_file(tmp_path):
    src = _solid(tmp_path / "s.png", (100, 100, 100))
    mask = tmp_path / "m.png"
    _rect_mask((64, 48), (5, 5, 20, 20)).save(mask)
    out = tmp_path / "o.png"
    outpaint.apply_local({"kind": "inpaint"}, {"original": str(src), "mask": str(mask)}, out)
    with Image.open(out) as im:
        assert im.size == (64, 48)
    with pytest.raises(ValueError):
        outpaint.apply_local({"kind": "x"}, {"original": str(src)}, tmp_path / "x.png")


# ---------- jobs: o resultado nunca mostra a marcação ----------

def test_outpaint_with_ai_fills_the_frame_and_keeps_the_original(me, ai_available, monkeypatch, login):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    monkeypatch.setattr(jobs, "_openrouter_image", lambda m, prompt, spec, refs: (_ai_png(GREEN, (200, 100)), 0.02))
    url = _mine(me)
    c = login(me["email"], c=TestClient(app))
    r = c.post("/api/image/outpaint", json={"source_url": url, "aspect": "16:9", "anchor": "center", "prompt": "céu"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "ai"
    job = started[0]
    W, H, x, y = outpaint.layout(64, 48, "16:9", "center")
    assert job["spec"]["composite"]["size"] == [W, H]
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    with Image.open(storage.resolve(job["output"], me)) as out:
        assert out.size == (W, H)
        assert out.crop((x, y, x + 64, y + 48)).tobytes() == Image.new("RGB", (64, 48), (30, 120, 200)).tobytes()
        assert out.getpixel((0, 0)) == GREEN  # área nova veio da IA


def test_inpaint_marking_never_leaks_into_the_result(me, ai_available, monkeypatch, login):
    """Pior caso: a IA devolve a própria imagem marcada (magenta em todo lugar). Nada fora da máscara muda."""
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    captured = {}

    def fake_ai(m, prompt, spec, refs):
        captured["refs"] = refs
        with Image.open(storage.resolve(spec.ref_image_urls[-1], me)) as painted:  # a pintada é a última entrada
            return _data_url(painted.convert("RGB")), 0.02  # devolve a entrada pintada, sem trocar nada
    monkeypatch.setattr(jobs, "_openrouter_image", fake_ai)
    url = _mine(me, color=(30, 120, 200))
    mask_img = _rect_mask((64, 48), (10, 10, 30, 30))
    c = login(me["email"], c=TestClient(app))
    r = c.post("/api/image/inpaint", json={"source_url": url, "mask": _data_url(mask_img.convert("RGB")), "prompt": "um vaso"})
    assert r.status_code == 200, r.text
    job = started[0]
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    with Image.open(storage.resolve(job["output"], me)) as out:
        assert out.size == (64, 48)
        assert out.getpixel((0, 0)) == (30, 120, 200)   # fora da máscara: idêntico ao original
        assert out.getpixel((63, 47)) == (30, 120, 200)
        # dentro: a região mudou (é o que a IA devolveu, com a cor ajustada ao entorno e a borda esfumada)
        assert out.getpixel((12, 12)) != (30, 120, 200)


def test_inpaint_with_ai_replaces_the_masked_region_only(me, ai_available, monkeypatch, login):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    monkeypatch.setattr(jobs, "_openrouter_image", lambda m, prompt, spec, refs: (_ai_png(GREEN, (64, 48)), 0.02))
    url = _mine(me, color=(30, 120, 200))
    c = login(me["email"], c=TestClient(app))
    r = c.post("/api/image/inpaint", json={"source_url": url, "mask": _data_url(_rect_mask((64, 48), (10, 10, 30, 30)).convert("RGB")),
                                           "prompt": "um vaso"})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me))
    with Image.open(storage.resolve(started[0]["output"], me)) as out:
        inside = out.getpixel((15, 15))                  # dentro da máscara: a IA, cor ajustada ao entorno
        assert sum(abs(a - b) for a, b in zip(inside, GREEN)) < sum(abs(a - b) for a, b in zip((30, 120, 200), GREEN))
        assert out.getpixel((0, 0)) == (30, 120, 200)    # fora: o original


def test_local_inpaint_runs_end_to_end_without_ai(me, no_ai, login):
    c = login(me["email"], c=TestClient(app))
    started = []
    import app.jobs as J
    orig = J.start
    J.start = lambda job, user, resume=False: started.append(job)
    try:
        url = _mine(me, color=(30, 120, 200))
        r = c.post("/api/image/inpaint", json={"source_url": url, "mask": _data_url(_rect_mask((64, 48), (10, 10, 30, 30)).convert("RGB")),
                                               "prompt": ""})
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "local"
    finally:
        J.start = orig
    asyncio.run(jobs._run(started[0], me))
    assert started[0]["status"] == "done", started[0].get("error")
    with Image.open(storage.resolve(started[0]["output"], me)) as out:
        assert out.getpixel((0, 0)) == (30, 120, 200)


def test_local_outpaint_runs_end_to_end_without_ai(me, no_ai, login, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c = login(me["email"], c=TestClient(app))
    url = _mine(me)
    r = c.post("/api/image/outpaint", json={"source_url": url, "aspect": "21:9", "anchor": "left"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    W, H, x, y = outpaint.layout(64, 48, "21:9", "left")
    asyncio.run(jobs._run(started[0], me))
    assert started[0]["status"] == "done", started[0].get("error")
    with Image.open(storage.resolve(started[0]["output"], me)) as out:
        assert out.size == (W, H)
        assert out.crop((x, y, x + 64, y + 48)).tobytes() == Image.new("RGB", (64, 48), (30, 120, 200)).tobytes()


# ---------- validações ----------

@pytest.mark.parametrize("payload", [{"aspect": "3:2"}, {"anchor": "diagonal"}, {"prompt": "x" * 1001}])
def test_outpaint_rejects_invalid_options(login, make_user, no_ai, payload):
    u = make_user(f"op-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    url = _mine(auth.public_user(u))
    assert c.post("/api/image/outpaint", json={"source_url": url, **payload}).status_code == 422


def test_inpaint_rejects_bad_or_empty_masks(login, make_user, no_ai):
    u = make_user(f"ip-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    url = _mine(me)
    assert c.post("/api/image/inpaint", json={"source_url": url, "mask": "nao-e-data-url"}).status_code == 400
    empty = _data_url(Image.new("RGB", (64, 48), (0, 0, 0)))
    r = c.post("/api/image/inpaint", json={"source_url": url, "mask": empty})
    assert r.status_code == 400 and "pinte" in r.json()["detail"].lower()


def test_source_must_belong_to_the_user(login, make_user, no_ai):
    c = login(make_user(f"op-own-{_uniq()}@test.local")["email"], c=TestClient(app))
    other = auth.public_user(make_user(f"outro-op-{_uniq()}@test.local"))
    assert c.post("/api/image/outpaint", json={"source_url": _mine(other)}).status_code == 403


def test_outpaint_keeps_the_original_exact_even_when_the_ai_returns_jpeg(me, ai_available, monkeypatch, login):
    """Regressão: a IA devolve JPEG; a recomposição tem de gravar PNG, senão o original muda por compressão."""
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), GREEN).save(buf, format="JPEG", quality=60)
    jpeg = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    monkeypatch.setattr(jobs, "_openrouter_image", lambda m, prompt, spec, refs: (jpeg, 0.02))
    url = _mine(me, color=(30, 120, 200))
    c = login(me["email"], c=TestClient(app))
    assert c.post("/api/image/outpaint", json={"source_url": url, "aspect": "16:9", "anchor": "center"}).status_code == 200
    job = started[0]
    W, H, x, y = outpaint.layout(64, 48, "16:9", "center")
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "done", job.get("error")
    assert job["output"].endswith(".png")
    with Image.open(storage.resolve(job["output"], me)) as out:
        assert out.crop((x, y, x + 64, y + 48)).tobytes() == Image.new("RGB", (64, 48), (30, 120, 200)).tobytes()
