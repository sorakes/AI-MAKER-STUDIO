"""Personagem falando: validação de áudio, animação Ken Burns com ffmpeg real (quando disponível) e provedor simulado."""
import asyncio
import io
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth, jobs, lipsync, media, providers, registry, storage, usage
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")

FAKE_LIP_MODEL = {"id": 21, "kind": "video", "label": "Lip sync simulado", "provider": "openrouter", "model_id": "fake/lip",
                  "caps": {"lipsync": True, "t2v": True, "i2v": True}, "fields": {"api": "fake"}, "params": {},
                  "cost": 0.05, "cost_unit": "second", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}


def _uniq():
    return str(time.time_ns())


def _image(path, color=(90, 140, 200), size=(160, 120)):
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _sine(path, seconds):
    subprocess.run([media._ff("ffmpeg"), "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    str(path)], capture_output=True, check=True, timeout=120)
    return path


@pytest.fixture
def me(make_user):
    return auth.public_user(make_user(f"lip-{_uniq()}@test.local"))


def _mine_image(me):
    return storage.to_url(_image(storage.user_dir(me["id"], "uploads") / f"ator-{_uniq()}.png"))


def _mine_audio(me, seconds=2):
    return storage.to_url(_sine(storage.user_dir(me["id"], "uploads") / f"fala-{_uniq()}.wav", seconds))


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda *a, **k: None)
    monkeypatch.setattr(registry, "for_user", lambda *a, **k: [])
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


@pytest.fixture
def lip_available(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda user, kind, model_id=None, need=None: FAKE_LIP_MODEL if (need or {}).get("lipsync") else None)
    monkeypatch.setattr(registry, "get", lambda mid: FAKE_LIP_MODEL)

    async def aget(mid):
        return FAKE_LIP_MODEL
    monkeypatch.setattr(registry, "aget", aget)
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [FAKE_LIP_MODEL])
    monkeypatch.setattr(registry, "estimate", lambda m, seconds=5: 0.05 * seconds)
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _client(login, make_user):
    u = make_user(f"lip-api-{_uniq()}@test.local")
    return login(u["email"], c=TestClient(app)), u


# ---------- sem ffmpeg ----------

def test_no_model_with_lipsync_capability_is_available_today(me, no_ai):
    assert lipsync.pick(me, "auto") is None


def test_voice_service_is_not_configured_and_says_so():
    assert lipsync.tts_available() is False
    with pytest.raises(providers.ProviderError, match="Sem voz"):
        lipsync.synthesize("oi", None)


def test_text_without_voice_is_rejected_with_a_clear_message(login, make_user, no_ai):
    c, u = _client(login, make_user)
    me = auth.public_user(u)
    r = c.post("/api/video/lipsync", json={"image_url": _mine_image(me), "script": "Olá, tudo bem?"})
    assert r.status_code == 400 and "Sem lip sync real" in r.json()["detail"]  # sem modelo real: pede o áudio


def test_request_needs_audio_or_script(login, make_user, no_ai):
    c, u = _client(login, make_user)
    me = auth.public_user(u)
    r = c.post("/api/video/lipsync", json={"image_url": _mine_image(me)})
    assert r.status_code == 400 and "áudio" in r.json()["detail"]


def test_image_must_be_an_image(login, make_user, no_ai):
    c, u = _client(login, make_user)
    txt = storage.user_dir(u["id"], "uploads") / "nao-e-imagem.png"
    txt.write_bytes(b"texto puro")
    assert c.post("/api/video/lipsync", json={"image_url": storage.to_url(txt), "audio_url": "/files/1/x.wav"}).status_code == 400


def test_audio_with_an_unsupported_extension_is_rejected(me):
    p = storage.user_dir(me["id"], "uploads") / "fala.txt"
    p.write_text("nao e audio")
    with pytest.raises(ValueError, match="Formato"):
        lipsync.audio_info(p)


def test_source_image_must_belong_to_the_user(login, make_user, no_ai):
    c, u = _client(login, make_user)
    other = auth.public_user(make_user(f"outro-lip-{_uniq()}@test.local"))
    assert c.post("/api/video/lipsync", json={"image_url": _mine_image(other), "script": "x"}).status_code == 403


# ---------- com ffmpeg real ----------

@needs_ffmpeg
def test_audio_info_reads_duration_with_ffprobe(tmp_path):
    p = _sine(tmp_path / "t.wav", 2)
    assert abs(lipsync.audio_info(p)["duration"] - 2.0) < 0.1


@needs_ffmpeg
def test_audio_longer_than_the_limit_is_rejected(tmp_path):
    p = _sine(tmp_path / "longo.wav", int(lipsync.MAX_AUDIO_S) + 2)
    with pytest.raises(ValueError, match="longo demais"):
        lipsync.audio_info(p)


@needs_ffmpeg
def test_kenburns_video_lasts_as_long_as_the_audio_and_has_sound(tmp_path):
    img = _image(tmp_path / "a.png")
    aud = _sine(tmp_path / "a.wav", 2)
    out = lipsync.kenburns(img, aud, tmp_path / "o.mp4", "9:16", 2.0)
    info = media.probe(out)
    assert abs(info["duration"] - 2.0) < 0.3
    assert (info["width"], info["height"]) == (720, 1280)
    streams = subprocess.run([media._ff("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams and "video" in streams


@needs_ffmpeg
def test_upload_validates_audio_and_removes_bad_files(login, make_user):
    c, u = _client(login, make_user)
    good = _sine(storage.user_dir(u["id"], "uploads").parent / "tmp-good.wav", 1)
    r = c.post("/api/upload", files={"file": ("fala.wav", good.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "audio" and abs(r.json()["duration"] - 1.0) < 0.2
    long = _sine(storage.user_dir(u["id"], "uploads").parent / "tmp-long.wav", int(lipsync.MAX_AUDIO_S) + 2)
    r2 = c.post("/api/upload", files={"file": ("longa.wav", long.read_bytes(), "audio/wav")})
    assert r2.status_code == 400 and "longo demais" in r2.json()["detail"]


@needs_ffmpeg
def test_local_lipsync_runs_end_to_end_with_ffmpeg(me, no_ai, login, make_user, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    c, u = _client(login, make_user)
    me2 = auth.public_user(u)
    r = c.post("/api/video/lipsync", json={"image_url": _mine_image(me2), "audio_url": _mine_audio(me2, 2), "aspect": "16:9"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    job = started[0]
    assert job["steps"][0]["label"] == "Animação simples"
    asyncio.run(jobs._run(job, me2))
    assert job["status"] == "done", job.get("error")
    info = media.probe(storage.resolve(job["output"], me2))
    assert abs(info["duration"] - 2.0) < 0.3 and (info["width"], info["height"]) == (1280, 720)


@needs_ffmpeg
def test_provider_path_is_used_when_a_lipsync_model_exists(me, lip_available, monkeypatch, login, make_user):
    """Provedor simulado: o gancho devolve um vídeo (aqui, uma animação real gerada com ffmpeg)."""
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    tmp = storage.user_dir(me["id"], "renders")
    fake_video = lipsync.kenburns(_image(tmp / "fake-src.png"), _sine(tmp / "fake-src.wav", 2), tmp / "fake-out.mp4", "16:9", 2.0)
    monkeypatch.setattr(lipsync, "remote", lambda m, image_url, audio_url, script: fake_video.read_bytes())
    c, u = _client(login, make_user)
    me2 = auth.public_user(u)
    r = c.post("/api/video/lipsync", json={"image_url": _mine_image(me2), "audio_url": _mine_audio(me2, 2)})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "ai"
    job = started[0]
    assert job["steps"][0]["label"] == "Fala com IA"
    asyncio.run(jobs._run(job, me2))
    assert job["status"] == "done", job.get("error")
    assert media.probe(storage.resolve(job["output"], me2))["duration"] > 1.5


def test_provider_failure_is_reported_on_the_job(me, lip_available, monkeypatch):
    """Sem fallback silencioso: se o provedor falha, o job mostra o erro (o fallback é decisão do pedido sem modelo)."""
    def boom(*a, **k):
        raise providers.ProviderError("provedor indisponível")
    monkeypatch.setattr(lipsync, "remote", boom)
    rr = jobs.RenderRequest(node_id="fala", gen="lipsync", prompt="p", keyframe_prompt="p", duration=2,
                            ref_image_urls=[_mine_image(me)], local_op={"kind": "lipsync", "audio_url": None, "aspect": "16:9", "seconds": 2})
    job = jobs.prepare(me, rr)
    asyncio.run(jobs._run(job, me))
    assert job["status"] == "error" and "provedor indisponível" in job["error"]
