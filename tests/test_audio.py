"""Áudio: validação, conversões com ffmpeg real (duração e não-silêncio), provedor simulado e endpoints/jobs."""
import asyncio
import base64
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import audio, auth, jobs, media, providers, storage, usage
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


def _uniq():
    return str(time.time_ns())


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _ffmake(path, args):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", *args, str(path)], check=True, capture_output=True)
    return path


def _pcm_tone(seconds=1.0):
    """PCM 16 bits, 24 kHz, mono de um tom (é o que a voz devolve)."""
    out = subprocess.run([media._ff("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i",
                          f"sine=frequency=440:duration={seconds}:sample_rate=24000", "-f", "s16le", "-ac", "1", "-"],
                         capture_output=True, check=True)
    return out.stdout


def _mean_db(path):
    r = subprocess.run([media._ff("ffmpeg"), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return -999.0


# ---------- puro: validação e prompts ----------

def test_speech_request_limits():
    audio.SpeechRequest(text="oi", voice="nova", speed=1.5)
    for bad in ({"text": ""}, {"text": "x" * 501}, {"text": "oi", "voice": "robo"}, {"text": "oi", "speed": 3}):
        with pytest.raises(ValidationError):
            audio.SpeechRequest(**bad)


def test_music_and_sfx_limits():
    with pytest.raises(ValidationError):
        audio.MusicRequest(seconds=4)
    with pytest.raises(ValidationError):
        audio.MusicRequest(genre="metal")
    with pytest.raises(ValidationError):
        audio.SfxRequest(kind="boom")
    with pytest.raises(ValidationError):
        audio.SfxRequest(seconds=11)


def test_music_prompt_says_no_vocals_and_carries_the_choices():
    p = audio.music_prompt(audio.MusicRequest(genre="jazz", mood="calm", seconds=12, prompt="piano suave"))
    assert "jazz calm instrumental" in p and "about 12 seconds" in p and "no vocals" in p and p.endswith("piano suave")


def test_speech_estimate_scales_with_text():
    assert audio.speech_seconds("oi") == 1.0
    assert audio.speech_seconds("x" * 140) == 10.0


def test_virtual_models_keep_the_real_provider_and_cost_hint():
    assert audio.GEN_MODEL["speech"]["model_id"] == audio.SPEECH_MODEL
    assert audio.GEN_MODEL["music"]["model_id"] == audio.MUSIC_MODEL
    assert audio.GEN_MODEL["sfx"]["provider"] == "ffmpeg"  # efeito não passa pelo OpenRouter
    assert set(jobs.VIRTUAL) >= {"audio-speech", "audio-music", "audio-sfx", "stt-audio-chat"}


# ---------- ffmpeg real ----------

@needs_ffmpeg
def test_pcm_becomes_mp3_with_the_right_length_and_speed(tmp_path):
    pcm = tmp_path / "v.pcm"
    pcm.write_bytes(_pcm_tone(1.0))
    normal, fast = tmp_path / "n.mp3", tmp_path / "f.mp3"
    audio.pcm_to_mp3(pcm, normal, 1.0)
    audio.pcm_to_mp3(pcm, fast, 2.0)
    assert abs(media.audio_duration(normal) - 1.0) < 0.1
    assert abs(media.audio_duration(fast) - 0.5) < 0.1  # 2× encurta pela metade
    assert _mean_db(normal) > -40  # não é silêncio


@needs_ffmpeg
def test_music_is_trimmed_to_the_requested_length(tmp_path):
    src = _ffmake(tmp_path / "clip.mp3", ["-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:a", "libmp3lame"])
    dest = tmp_path / "m.mp3"
    audio.trim_mp3(src, dest, 10)
    assert abs(media.audio_duration(dest) - 10.0) < 0.2
    assert _mean_db(dest) > -40


@needs_ffmpeg
@pytest.mark.parametrize("kind", audio.SFX_KINDS)
def test_every_sound_effect_is_synthesized_with_audio(tmp_path, kind):
    dest = tmp_path / f"{kind}.mp3"
    audio.synth_sfx(kind, 2.0, dest)
    assert abs(media.audio_duration(dest) - 2.0) < 0.15
    assert _mean_db(dest) > -50, kind  # não é silêncio


# ---------- run(): provedor simulado, custo vem do provedor ----------

@needs_ffmpeg
def test_run_speech_uses_provider_audio_and_returns_its_cost(tmp_path, monkeypatch):
    pcm = _pcm_tone(1.0)
    monkeypatch.setattr(providers, "openrouter_audio",
                        lambda model, messages, **k: (pcm, "olá", 0.000297) if model == audio.SPEECH_MODEL else (b"", "", 0))
    dest = tmp_path / "fala.mp3"
    spec = SimpleNamespace(prompt="olá", local_op={"voice": "nova", "speed": 1.0})
    cost = audio.run("speech", spec, dest, audio.SPEECH_LOCAL)
    assert cost == 0.000297 and abs(media.audio_duration(dest) - 1.0) < 0.1


@needs_ffmpeg
def test_run_music_trims_the_provider_clip(tmp_path, monkeypatch):
    clip = _ffmake(tmp_path / "src.mp3", ["-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:a", "libmp3lame"])
    monkeypatch.setattr(providers, "openrouter_audio", lambda model, messages, **k: (clip.read_bytes(), "", 0.04))
    dest = tmp_path / "musica.mp3"
    cost = audio.run("music", SimpleNamespace(prompt="x", local_op={"seconds": 10}), dest, audio.MUSIC_LOCAL)
    assert cost == 0.04 and abs(media.audio_duration(dest) - 10.0) < 0.2


def test_run_speech_without_audio_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "openrouter_audio", lambda *a, **k: (b"", "não sei", 0.0))
    with pytest.raises(audio.AudioError, match="não devolveu áudio"):
        audio.run("speech", SimpleNamespace(prompt="x", local_op={"voice": "alloy", "speed": 1}), tmp_path / "x.mp3",
                  audio.SPEECH_LOCAL)


# ---------- endpoints e jobs ----------

def _job_runs(monkeypatch, login, make_user, path, body):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    u = make_user(f"aud-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    r = c.post(path, json=body)
    return r, started, me


def test_audio_endpoints_validate_input(login, make_user, no_ai):
    u = make_user(f"aud-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    assert c.post("/api/audio/speech", json={"text": ""}).status_code == 422
    assert c.post("/api/audio/music", json={"seconds": 99}).status_code == 422
    assert c.post("/api/audio/sfx", json={"kind": "boom"}).status_code == 422
    assert TestClient(app).post("/api/audio/speech", json={"text": "oi"}).status_code == 401


@needs_ffmpeg
def test_speech_job_end_to_end_keeps_provider_cost(login, make_user, no_ai, monkeypatch):
    pcm = _pcm_tone(1.0)
    monkeypatch.setattr(providers, "openrouter_audio", lambda model, messages, **k: (pcm, "olá", 0.000297))
    r, started, me = _job_runs(monkeypatch, login, make_user, "/api/audio/speech",
                               {"text": "Bem-vindo à nova coleção de verão.", "voice": "nova", "speed": 1.0})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me))
    job = started[0]
    assert job["status"] == "done", job.get("error")
    assert job["media"]["kind"] == "audio" and job["media"]["duration"] > 0
    assert abs(sum(s["cost"] for s in job["steps"]) - 0.000297) < 1e-9  # custo real do provedor, não a estimativa
    assert job["output"].endswith(".mp3")


@needs_ffmpeg
def test_music_job_writes_a_trimmed_clip(login, make_user, no_ai, monkeypatch):
    clip = _ffmake(storage.user_dir(1, "renders") / f"fonte-{_uniq()}.mp3",
                   ["-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:a", "libmp3lame"])
    monkeypatch.setattr(providers, "openrouter_audio", lambda model, messages, **k: (clip.read_bytes(), "", 0.04))
    r, started, me = _job_runs(monkeypatch, login, make_user, "/api/audio/music",
                               {"genre": "pop", "mood": "happy", "seconds": 10, "prompt": "violão"})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me))
    assert started[0]["status"] == "done", started[0].get("error")
    assert abs(started[0]["media"]["duration"] - 10.0) < 0.2


@needs_ffmpeg
def test_sfx_job_needs_no_provider(login, make_user, no_ai, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("efeito não deve chamar provedor")
    monkeypatch.setattr(providers, "openrouter_audio", boom)
    r, started, me = _job_runs(monkeypatch, login, make_user, "/api/audio/sfx", {"kind": "beep", "seconds": 2})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me))
    job = started[0]
    assert job["status"] == "done", job.get("error")
    assert job["steps"][0]["cost"] == 0.0 and abs(job["media"]["duration"] - 2.0) < 0.15


# ---------- parser do stream real (sem rede): o caminho que o provedor usa de verdade ----------

class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_openrouter_audio_parses_the_streamed_sse_chunks(monkeypatch):
    chunk1 = base64.b64encode(b"\x01\x02\x03\x04").decode()
    chunk2 = base64.b64encode(b"\x05\x06").decode()
    lines = [
        f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{chunk1}"}}}}}}]}}\n'.encode(),
        f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{chunk2}","transcript":"oi"}}}}}}]}}\n'.encode(),
        b'data: {"choices":[],"usage":{"cost":0.000297}}\n',
        b"data: [DONE]\n",
    ]
    monkeypatch.setattr(providers, "get_key", lambda *a, **k: "chave-de-teste")
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda req, timeout=None: _FakeStream(lines))
    data, text, cost = providers.openrouter_audio(audio.SPEECH_MODEL, audio.speech_messages("oi"),
                                                  audio={"voice": "alloy", "format": "pcm16"})
    assert data == b"\x01\x02\x03\x04\x05\x06"  # os pedaços base64 são juntados e decodificados
    assert text == "oi" and cost == 0.000297


def test_openrouter_audio_reports_an_error_event_as_provider_error(monkeypatch):
    monkeypatch.setattr(providers, "get_key", lambda *a, **k: "chave-de-teste")
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeStream([b'data: {"error":{"message":"limite excedido"}}\n']))
    with pytest.raises(providers.ProviderError, match="limite excedido"):
        providers.openrouter_audio(audio.MUSIC_MODEL, [{"role": "user", "content": "x"}])
