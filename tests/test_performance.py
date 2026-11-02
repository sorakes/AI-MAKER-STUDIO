"""Performance: duração e prompt, validação, cotação com confirmação, Fala sem modelo real, e um job completo com o
provedor simulado (corpo enviado conferido, MP4 real com ffmpeg)."""
import asyncio
import json
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app import auth, jobs, media, performance, providers, registry, registry as reg, storage, usage
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


def _uniq():
    return str(time.time_ns())


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _fake_model(monkeypatch, available=True):
    fake = {"id": 999, "kind": "video", "label": "Seedance 2.0 Fast (teste)", "provider": "openrouter",
            "model_id": performance.PERF_MODEL_ID, "caps": {"prompt": True, "i2v": True, "audio": True, "durations": [4.0, 15.0]},
            "fields": {"api": "openrouter_video", "resolutions": ["480p", "720p"], "aspects": ["16:9", "9:16", "1:1"]},
            "params": {}, "cost": 0.0907, "cost_unit": "second", "key_id": None, "key_name": "", "is_default": 0,
            "enabled": 1, "available": available}
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [fake] if available else [])
    monkeypatch.setattr(registry, "get", lambda mid, *a, **k: fake if mid == fake["id"] else None)

    async def aget(mid, *a, **k):
        return fake if mid == fake["id"] else None
    monkeypatch.setattr(registry, "aget", aget)
    return fake


def _face(path):
    Image.new("RGB", (320, 240), (200, 160, 130)).save(path)
    return path


# ---------- regras puras ----------

def test_duration_is_an_integer_between_four_and_fifteen_seconds():
    assert performance.seconds_for("oi") == 4                    # curto: mínimo do modelo
    assert performance.seconds_for("x" * 300) == 15               # longo: teto
    assert performance.seconds_for("x" * 60) == 5                 # ~60 caracteres = ~4,3 s -> 5
    assert performance.seconds_for("oi", audio_seconds=6.2) == 7  # áudio ligado: arredonda para cima


def test_prompt_carries_the_speech_and_keeps_the_identity():
    req = performance.PerformanceRequest(image_url="/files/1/x.png", text="Bem-vindo à coleção", motion="sorri")
    p = performance.prompt_for(req)
    assert 'saying: "Bem-vindo à coleção"' in p and "Keep the face, clothes and background identical" in p and "sorri" in p


def test_request_validates_text_and_aspect():
    with pytest.raises(ValidationError):
        performance.PerformanceRequest(image_url="/f.png", text="")
    with pytest.raises(ValidationError):
        performance.PerformanceRequest(image_url="/f.png", text="oi", aspect="4:3")


def test_registry_sends_generate_audio_only_when_asked():
    m = {"model_id": "x/y", "caps": {"audio": True}, "fields": {"resolutions": ["480p", "720p"]}, "params": {}}
    on = reg.build_or_video_input(m, prompt="p", start=None, end=None, refs=[], aspect="16:9", duration=4,
                                  quality="draft", seed=None, generate_audio=True)
    off = reg.build_or_video_input(m, prompt="p", start=None, end=None, refs=[], aspect="16:9", duration=4,
                                   quality="draft", seed=None)
    assert on["generate_audio"] is True and on["resolution"] == "480p" and off["generate_audio"] is False


# ---------- endpoints ----------

def test_quote_reports_seconds_and_confirmation_threshold(login, make_user, no_ai, monkeypatch):
    _fake_model(monkeypatch)
    u = make_user(f"perf-q-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    img = storage.to_url(_face(storage.user_dir(me["id"], "uploads") / f"f-{_uniq()}.png"))
    r = c.post("/api/video/performance/quote", json={"image_url": img, "text": "oi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seconds"] == 4 and abs(body["usd"] - 4 * 0.0907) < 1e-6
    assert body["confirm"] is True  # US$ 0,36 > US$ 0,25: exige confirmação


def test_without_a_real_model_the_fala_is_told_to_send_audio(login, make_user, no_ai, monkeypatch):
    _fake_model(monkeypatch, available=False)
    u = make_user(f"perf-none-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    img = storage.to_url(_face(storage.user_dir(me["id"], "uploads") / f"n-{_uniq()}.png"))
    r = c.post("/api/video/lipsync", json={"image_url": img, "audio_url": None, "script": "oi pessoal"})
    assert r.status_code == 400 and "Sem lip sync real hoje" in r.json()["detail"]
    assert c.post("/api/video/performance", json={"image_url": img, "text": "oi"}).status_code == 400


@needs_ffmpeg
def test_performance_job_sends_the_right_body_and_returns_a_video(login, make_user, no_ai, monkeypatch, tmp_path):
    _fake_model(monkeypatch)
    sent = {}
    clip = tmp_path / "saida.mp4"
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(clip)], check=True, capture_output=True)

    def fake_submit(body, key_id=None):
        sent["body"] = body
        return {"provider": "openrouter", "id": "gen-1", "polling_url": "/api/v1/videos/gen-1", "key_id": None}
    monkeypatch.setattr(providers, "or_video_submit", fake_submit)
    monkeypatch.setattr(providers, "or_video_status", lambda h: {"status": "completed"})
    monkeypatch.setattr(providers, "or_video_download", lambda h, s: clip.read_bytes())
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))

    u = make_user(f"perf-job-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    img = storage.to_url(_face(storage.user_dir(me["id"], "uploads") / f"j-{_uniq()}.png"))
    r = c.post("/api/video/performance", json={"image_url": img, "text": "Bem-vindo à coleção de verão", "aspect": "16:9"})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me))
    job = started[0]
    assert job["status"] == "done", job.get("error")
    body = sent["body"]
    assert body["model"] == performance.PERF_MODEL_ID and body["duration"] == 4 and body["resolution"] == "480p"
    assert body["generate_audio"] is True and "Bem-vindo à coleção de verão" in body["prompt"]
    assert "data:image" in json.dumps(body)  # a imagem do personagem vai como dado, nunca por URL pública
    assert abs(media.probe(storage.resolve(job["output"], me))["duration"] - 4.0) < 0.3
