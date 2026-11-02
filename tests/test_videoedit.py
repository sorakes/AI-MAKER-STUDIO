"""Editar vídeo (fallback por quadros): regras de trecho, instrução, remontagem com ffmpeg real e endpoints
com o modelo de imagem simulado."""
import asyncio
import base64
import io
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app import auth, jobs, media, registry, storage, usage, videoedit
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


def _uniq():
    return str(time.time_ns())


def _clip(path, seconds=4):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=320x180:rate=25", "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True, capture_output=True)
    return path


def _red_png_uri(size=(320, 180)):
    buf = io.BytesIO()
    Image.new("RGB", size, (220, 30, 30)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _frame(video, t, out):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                    str(out)], check=True, capture_output=True)
    return Image.open(out).convert("RGB")


def _mean(img):
    px = list(img.getdata())
    return tuple(sum(p[i] for p in px) // len(px) for i in range(3))


# ---------- regras puras ----------

def test_segment_rules_reject_bad_ranges():
    videoedit.validate_segment(0, 3, 4)
    with pytest.raises(ValueError, match="fim do trecho"):
        videoedit.validate_segment(2, 2, 4)
    with pytest.raises(ValueError, match="no máximo 10"):
        videoedit.validate_segment(0, 11, 30)
    with pytest.raises(ValueError, match="depois do fim"):
        videoedit.validate_segment(5, 6, 4)


def test_frame_count_is_one_per_second_between_two_and_six():
    assert [videoedit.frame_count(s) for s in (0.5, 3, 4.4, 9)] == [2, 3, 4, 6]


def test_instruction_carries_preset_prompt_and_strength_and_keeps_the_rest():
    req = videoedit.VideoEditRequest(source_url="/files/1/x.mp4", prompt="troque o céu", preset="sunset", strength="high")
    text = videoedit.instruction(req)
    assert "golden sunset" in text and "troque o céu" in text and "strongly" in text
    assert "Keep everything else identical" in text


def test_request_validates_preset_and_range():
    with pytest.raises(ValidationError):
        videoedit.VideoEditRequest(source_url="/f", preset="neon")
    with pytest.raises(ValidationError):
        videoedit.VideoEditRequest(source_url="/f", end=0)


def test_estimate_is_frames_times_image_cost():
    assert videoedit.estimate(3, 0.034) == {"frames": 3, "usd": 0.102}


# ---------- ffmpeg real com quadros simulados ----------

@needs_ffmpeg
def _renders(tmp_path):
    """Quadros são URLs do armazenamento: o trabalho fica numa pasta de renders do usuário de teste."""
    d = storage.user_dir(1, "renders") / f"ve-{_uniq()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_run_edits_only_the_segment_and_keeps_audio_and_length(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 4)
    dest = _renders(tmp_path) / "saida.mp4"

    def fake_edit(prompt, frame_url, prev=None):
        assert "sunset" in prompt and frame_url  # a instrução e o quadro chegam ao modelo
        return _red_png_uri(), 0.034

    cost = videoedit.run(src, {"start": 1.0, "end": 3.0, "prompt": "", "preset": "sunset", "strength": "medium",
                               "preserve_audio": True, "user_id": 1}, dest, fake_edit)
    assert abs(cost - 2 * 0.034) < 1e-9  # 2 quadros (trecho de 2 s)
    info = media.probe(dest)
    assert abs(info["duration"] - 4.0) < 0.3
    assert info["width"] and info["height"]
    assert "audio" in subprocess.run([media._ff("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
                                      "-of", "csv=p=0", str(dest)], capture_output=True, text=True).stdout
    edited = _frame(dest, 2.0, tmp_path / "meio.png")  # dentro do trecho: vermelho do quadro simulado
    assert _mean(edited)[0] > _mean(edited)[1] + 40
    original_head = _mean(_frame(src, 0.3, tmp_path / "a.png"))
    kept_head = _mean(_frame(dest, 0.3, tmp_path / "b.png"))
    assert all(abs(a - b) < 20 for a, b in zip(original_head, kept_head))  # antes do trecho: o original, sem tocar


@needs_ffmpeg
def test_run_without_audio_option_drops_the_track(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 3)
    dest = _renders(tmp_path) / "mudo.mp4"
    videoedit.run(src, {"start": 0, "end": 2, "prompt": "", "preset": "night", "strength": "low",
                        "preserve_audio": False, "user_id": 1}, dest, lambda p, u, prev=None: (_red_png_uri(), 0.01))
    streams = subprocess.run([media._ff("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(dest)], capture_output=True, text=True).stdout
    assert "audio" not in streams and abs(media.probe(dest)["duration"] - 3.0) < 0.3


# ---------- endpoints e jobs com o modelo de imagem simulado ----------

@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _fake_image_model(monkeypatch):
    fake = {"id": "img-fake", "kind": "image", "label": "Imagem barata (teste)", "provider": "openrouter",
            "model_id": "x/y", "caps": {"t2i": True, "refs": True, "max_refs": 4}, "fields": {}, "params": {},
            "cost": 0.034, "cost_unit": "run", "key_id": None, "key_name": "", "is_default": 1, "enabled": 1}
    monkeypatch.setattr(registry, "pick", lambda *a, **k: fake)
    monkeypatch.setattr(registry, "get", lambda mid, *a, **k: fake if mid == fake["id"] else None)

    async def aget(mid, *a, **k):
        return fake if mid == fake["id"] else None
    monkeypatch.setattr(registry, "aget", aget)
    return fake


def test_api_no_longer_offers_the_frames_path(login, make_user, no_ai, monkeypatch):
    """O node só usa modelos que recebem o vídeo: 'por quadros' e 'sem túnel' viram 400 com motivo claro."""
    from app import tunnel
    _fake_image_model(monkeypatch)
    u = make_user(f"ve-q-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    url = storage.to_url(_clip(storage.user_dir(me["id"], "uploads") / f"q-{_uniq()}.mp4", 4))
    monkeypatch.setattr(tunnel, "public_host", lambda: "")
    r = c.post("/api/video/edit/quote", json={"source_url": url, "start": 0, "end": 4, "preset": "anime"})
    assert r.status_code == 400 and "túnel" in r.json()["detail"]
    monkeypatch.setattr(tunnel, "public_host", lambda: "h.trycloudflare.com")
    r = c.post("/api/video/edit", json={"source_url": url, "whole": True, "preset": "anime", "model": "frames"})
    assert r.status_code == 400 and "recebem o vídeo" in r.json()["detail"]
