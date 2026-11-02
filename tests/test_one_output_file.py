"""Um job = um arquivo em renders: o bruto do provedor fica na pasta oculta e só o resultado final é publicado."""
import io

import pytest
from PIL import Image

from app import auth, jobs, storage


def _png_bytes(color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def me(make_user):
    user = auth.public_user(make_user("one-output-file@test.local"))
    storage.CURRENT.set(user)  # como o _run faz: resolve() precisa do dono do job
    return user


def _renders(uid):
    return sorted(p.name for p in storage.user_dir(uid, "renders").iterdir())


def test_hidden_store_keeps_the_provider_raw_out_of_renders(me, monkeypatch):
    monkeypatch.setattr(jobs.providers, "download", lambda url: _png_bytes())
    job = {"id": "job-hidden1", "user_id": me["id"]}
    url = jobs._store(job, 0, "https://provedor.test/saida.jpg", "image", True)
    raw = storage.resolve(url)
    assert raw.parent.name == "_tmp"  # pasta oculta
    assert not any(n.startswith("job-hidden1") for n in _renders(me["id"]))


def test_plain_store_still_writes_the_render_directly(me, monkeypatch):
    monkeypatch.setattr(jobs.providers, "download", lambda url: _png_bytes())
    job = {"id": "job-plain1", "user_id": me["id"]}
    url = jobs._store(job, 0, "https://provedor.test/saida.png", "image")
    assert storage.resolve(url).parent.name == "renders"
    assert "job-plain1_0.png" in _renders(me["id"])


def test_publish_moves_the_final_file_to_renders_and_deletes_the_raw(me):
    job = {"id": "job-pub1", "user_id": me["id"]}
    raw = storage.temp_file(me["id"], "_job-pub1_0_raw.jpg")
    raw.write_bytes(_png_bytes((200, 0, 0)))
    composed = raw.with_suffix(".png")  # a composição grava o final ao lado do bruto
    composed.write_bytes(_png_bytes((0, 200, 0)))

    url = jobs._publish(job, 0, storage.to_url(composed), raw)

    names = [n for n in _renders(me["id"]) if n.startswith("job-pub1")]
    assert names == ["job-pub1_0.png"]  # um arquivo só, e é o final
    assert storage.resolve(url).name == "job-pub1_0.png"
    assert not raw.exists() and not composed.exists()


def test_publish_without_raw_leftovers_just_moves_the_result(me):
    job = {"id": "job-pub2", "user_id": me["id"]}
    final = storage.temp_file(me["id"], "_job-pub2_0_raw.png")
    final.write_bytes(_png_bytes())
    url = jobs._publish(job, 0, storage.to_url(final), final)  # o bruto é o próprio final (ajuste de tamanho)
    assert storage.resolve(url).name == "job-pub2_0.png"
    assert [n for n in _renders(me["id"]) if n.startswith("job-pub2")] == ["job-pub2_0.png"]
