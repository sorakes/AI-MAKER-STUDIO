"""Arquivos de trabalho dos editores de desenho (máscaras, quadros anotados) ficam na pasta oculta _tmp: fora de
Meus arquivos, fora da cota, apagados por idade, e as rotas nunca gravam nelas em renders/uploads."""
import base64
import io
import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth, jobs, storage, usage
from app.main import app
from app.routers import studio as studio_mod

UID = 9311


def _png_data_url(size=(64, 64), box=None):
    img = Image.new("L", size, 0)
    if box:
        img.paste(255, box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def clean_user_dir():
    import shutil
    d = storage.USERS_DIR / str(UID)
    shutil.rmtree(d, ignore_errors=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_listing_never_shows_the_hidden_work_folder(clean_user_dir):
    tmp = storage.user_dir(UID, storage.TEMP_DIR) / "mascara.png"
    tmp.write_bytes(b"x" * 10)
    storage.user_dir(UID, "renders").joinpath("job-1_0.png").write_bytes(b"y")
    names = [f["name"] for f in storage.list_files(UID)]
    assert "job-1_0.png" in names and "mascara.png" not in names


def test_old_temp_files_are_removed_and_new_ones_kept(clean_user_dir):
    d = storage.user_dir(UID, storage.TEMP_DIR)
    old, new = d / "velha.png", d / "nova.png"
    old.write_bytes(b"a")
    new.write_bytes(b"b")
    two_days = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days, two_days))
    assert storage.cleanup_temp(UID) == 1
    assert not old.exists() and new.exists()


def test_quota_does_not_count_the_work_folder(clean_user_dir):
    d = storage.user_dir(UID, storage.TEMP_DIR)
    (d / "grande.bin").write_bytes(b"x" * 300_000)
    assert storage.used_bytes(UID) == 0
    storage.user_dir(UID, "renders").joinpath("r.bin").write_bytes(b"x" * 1000)
    assert storage.used_bytes(UID) == 1000


def test_data_url_is_validated_before_it_is_stored(clean_user_dir):
    p = storage.save_temp_data_url(UID, _png_data_url())
    assert p.parent.name == storage.TEMP_DIR and p.suffix == ".png" and p.exists()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        storage.save_temp_data_url(UID, "data:text/html;base64,PGgxPg==")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        storage.save_temp_data_url(UID, "data:image/png;base64," + base64.b64encode(b"x" * (storage.TEMP_MAX_BYTES + 1)).decode())
    assert e.value.status_code == 413


@pytest.fixture
def owner(make_user, login, monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)
    u = make_user(f"tmp-{time.time_ns()}@test.local")
    return u, login(u["email"], c=TestClient(app))


def test_inpaint_writes_its_mask_and_marked_image_only_to_the_work_folder(owner, monkeypatch, clean_user_dir):
    u, c = owner
    me = auth.public_user(u)
    src = storage.user_dir(me["id"], "uploads") / "origem.png"
    Image.new("RGB", (64, 64), (80, 120, 200)).save(src)
    captured = {}

    async def fake_image_job(user, rr, edit=False):
        captured["rr"] = rr
        return {"id": "x"}
    monkeypatch.setattr(studio_mod, "_image_job", fake_image_job)
    r = c.post("/api/image/inpaint", json={"node_id": "n", "name": "n", "source_url": storage.to_url(src),
                                           "mask": _png_data_url(box=(10, 10, 30, 30)), "prompt": "troque", "model": "auto"})
    assert r.status_code == 200, r.text
    mask_url = captured["rr"].local_op["mask_url"]
    assert "/_tmp/" in mask_url and "/renders/" not in mask_url
    assert storage.resolve(mask_url, me).exists()
    shown = [f["name"] for f in storage.list_files(me["id"])]
    assert all("_mask" not in n and "_marked" not in n and "_keep" not in n for n in shown)


def test_render_moves_a_data_url_direction_diagram_to_the_work_folder(owner, monkeypatch, clean_user_dir):
    u, c = owner
    me = auth.public_user(u)
    seen = {}

    def fake_prepare(user, req):
        seen["url"] = req.direction_image_url
        return {"id": "j1", "steps": [], "status": "queued", "node_id": req.node_id}
    monkeypatch.setattr(jobs, "prepare", fake_prepare)
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: None)
    r = c.post("/api/render", json={"node_id": "n", "name": "n", "gen": "video", "model": "auto", "prompt": "p",
                                    "direction_image_url": _png_data_url()})
    assert r.status_code == 200, r.text
    assert "/_tmp/" in seen["url"] and storage.resolve(seen["url"], me).exists()
