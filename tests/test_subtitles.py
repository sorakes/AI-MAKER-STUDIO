"""Legendas: parsers SRT/VTT, ASS e estilos (puro), tradução e STT com provedor simulado, queima com ffmpeg real."""
import asyncio
import json
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from PIL import Image, ImageChops

from app import auth, jobs, llm, media, registry, storage, subtitles, usage
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


def _uniq():
    return str(time.time_ns())


def _clip(path, seconds=3, audio=True):
    cmd = [media._ff("ffmpeg"), "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x180:rate=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else ["-an"]) + [str(path)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    return path


@pytest.fixture
def me(make_user):
    return auth.public_user(make_user(f"subs-{_uniq()}@test.local"))


def _mine_video(me, seconds=3, audio=True):
    p = storage.user_dir(me["id"], "uploads") / f"clipe-{_uniq()}.mp4"
    _clip(p, seconds, audio)
    return storage.to_url(p), p


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


# ---------- puro: parsers, SRT e ASS ----------

SRT = """1
00:00:01,000 --> 00:00:02,500
Olá, mundo

2
00:00:03,000 --> 00:00:04,000
<i>segunda</i> linha
"""

VTT = """WEBVTT

00:01.000 --> 00:02.500
Olá, mundo

00:00:03.000 --> 00:00:04.000
segunda linha
"""


def test_parse_srt_reads_times_and_text():
    segs = subtitles.parse_subs(SRT)
    assert segs == [{"start": 1.0, "end": 2.5, "text": "Olá, mundo"},
                    {"start": 3.0, "end": 4.0, "text": "segunda linha"}]


def test_parse_vtt_accepts_short_times_and_header():
    segs = subtitles.parse_subs(VTT)
    assert [(s["start"], s["end"]) for s in segs] == [(1.0, 2.5), (3.0, 4.0)]


def test_srt_export_round_trips():
    segs = subtitles.parse_subs(SRT)
    out = subtitles.to_srt(segs)
    assert "00:00:01,000 --> 00:00:02,500" in out and out.startswith("1\n")
    assert subtitles.parse_subs(out) == segs


def test_clean_segments_clamps_and_rejects_empty():
    segs = subtitles.clean_segments([{"start": 2.0, "end": 9.0, "text": "fim"}, {"start": 0.0, "end": 1.0, "text": "  "},
                                     {"start": 0.5, "end": 1.0, "text": "início"}], duration=3.0)
    assert segs == [{"start": 0.5, "end": 1.0, "text": "início"}, {"start": 2.0, "end": 3.0, "text": "fim"}]
    with pytest.raises(ValueError):
        subtitles.clean_segments([{"start": 5.0, "end": 6.0, "text": "fora"}], duration=3.0)


def test_styles_produce_distinct_ass():
    segs = [{"start": 0.0, "end": 2.0, "text": "bom dia pessoal"}]
    classic = subtitles.to_ass(segs, "classic")
    shorts = subtitles.to_ass(segs, "shorts")
    karaoke = subtitles.to_ass(segs, "karaoke")
    minimal = subtitles.to_ass(segs, "minimal")
    assert "BOM DIA PESSOAL" in shorts and "BOM DIA PESSOAL" not in classic  # caixa alta só no shorts
    assert "\\kf" in karaoke and "\\kf" not in classic  # destaque palavra por palavra
    assert len({classic, shorts, karaoke, minimal}) == 4


def test_position_and_color_map_to_ass():
    segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
    top = subtitles.to_ass(segs, "classic", position="top", color="#ff0000")
    assert ",8," in top  # alinhamento 8 = topo centro
    assert "&H000000FF" in top  # #ff0000 vira BGR no ASS
    assert ",2," in subtitles.to_ass(segs, "classic", position="bottom")


def test_request_validation_rejects_bad_options():
    base = {"source_url": "/files/1/x.mp4"}
    with pytest.raises(ValidationError):
        subtitles.SubtitleRequest(**base, style="neon")
    with pytest.raises(ValidationError):
        subtitles.SubtitleRequest(**base, color="vermelho")
    with pytest.raises(ValidationError):
        subtitles.SubtitleRequest(**base, translate_to="fr")
    with pytest.raises(ValidationError):
        subtitles.Segment(start=-1, end=2, text="x")  # início negativo


def test_stt_json_is_parsed_even_inside_fences():
    raw = '```json\n{"language":"pt","segments":[{"start":0,"end":1.5,"text":"oi"},{"start":2,"end":3,"text":" "}]}\n```'
    out = subtitles.parse_stt(raw)
    assert out["language"] == "pt" and out["segments"] == [{"start": 0.0, "end": 1.5, "text": "oi"}]
    with pytest.raises(Exception):
        subtitles.parse_stt("não é json")


def test_translation_keeps_order_and_length(monkeypatch):
    segs = [{"start": 0, "end": 1, "text": "bom dia"}, {"start": 1, "end": 2, "text": "tchau"}]
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps({"texts": ["good morning", "bye"]}))
    out = subtitles.translate(segs, "en")
    assert [s["text"] for s in out] == ["good morning", "bye"]
    assert [(s["start"], s["end"]) for s in out] == [(0, 1), (1, 2)]


def test_translation_refuses_mismatched_or_missing_model(monkeypatch):
    segs = [{"start": 0, "end": 1, "text": "a"}, {"start": 1, "end": 2, "text": "b"}]
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps({"texts": ["só uma"]}))
    with pytest.raises(ValueError, match="número de linhas"):
        subtitles.translate(segs, "pt")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: None)
    with pytest.raises(ValueError, match="Tradução indisponível"):
        subtitles.translate(segs, "pt")


# ---------- endpoints e jobs (provedor simulado) ----------

def test_subtitles_endpoint_validates_segments(login, make_user, no_ai):
    u = make_user(f"subs-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    img = storage.user_dir(u["id"], "uploads") / "x.png"
    Image.new("RGB", (8, 8)).save(img)
    r = c.post("/api/video/subtitles", json={"source_url": storage.to_url(img), "segments": [{"start": 0, "end": 1, "text": "x"}]})
    assert r.status_code == 400  # não é vídeo


@needs_ffmpeg
def test_subtitles_endpoint_rejects_empty_and_foreign(login, make_user, no_ai):
    u = make_user(f"subs-empty-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    url, _ = _mine_video(auth.public_user(u))
    assert c.post("/api/video/subtitles", json={"source_url": url}).status_code == 400  # sem segmentos
    assert c.post("/api/video/subtitles", json={"source_url": url, "segments": [{"start": 9, "end": 10, "text": "x"}]}).status_code == 400
    other = auth.public_user(make_user(f"outro-subs-{_uniq()}@test.local"))
    foreign, _ = _mine_video(other)
    assert c.post("/api/video/subtitles", json={"source_url": foreign, "segments": [{"start": 0, "end": 1, "text": "x"}]}).status_code == 403


@needs_ffmpeg
def test_transcribe_endpoint_stores_segments_on_the_job(login, make_user, no_ai, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    monkeypatch.setattr(subtitles, "stt_call", lambda wav_b64, language="": json.dumps(
        {"language": "pt", "segments": [{"start": 0.2, "end": 1.4, "text": "olá pessoal"}]}))
    u = make_user(f"subs-stt-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    url, _ = _mine_video(me2)
    r = c.post("/api/video/transcribe", json={"source_url": url, "language": "pt"})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me2))
    assert started[0]["status"] == "done", started[0].get("error")
    assert started[0]["result"]["segments"] == [{"start": 0.2, "end": 1.4, "text": "olá pessoal"}]
    assert started[0]["output"] is None  # transcrição não gera vídeo


@needs_ffmpeg
def test_transcribe_refuses_video_without_audio(login, make_user, no_ai):
    u = make_user(f"subs-mudo-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    p = storage.user_dir(me2["id"], "uploads") / "mudo.mp4"
    _clip(p, 2, audio=False)
    assert c.post("/api/video/transcribe", json={"source_url": storage.to_url(p)}).status_code == 400


@needs_ffmpeg
def test_burn_in_changes_the_bottom_of_the_frame_and_keeps_length(tmp_path):
    src = _clip(tmp_path / "base.mp4", 3, audio=True)
    dest = tmp_path / "queimado.mp4"
    op = {"segments": [{"start": 0.5, "end": 2.5, "text": "LEGENDA DE TESTE"}], "style": "classic", "position": "bottom",
          "size": "large", "color": "#ffffff", "highlight": "#facc15"}
    srt = subtitles.burn(src, op, dest)
    assert "LEGENDA DE TESTE" in srt.read_text(encoding="utf-8")
    assert abs(media.probe(dest)["duration"] - 3.0) < 0.3

    def frame(video, t, name):
        out = tmp_path / name
        subprocess.run([media._ff("ffmpeg"), "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(out)],
                       capture_output=True, check=True)
        return Image.open(out).convert("RGB")
    a = frame(src, 1.5, "antes.png")
    b = frame(dest, 1.5, "depois.png")
    band = (0, int(a.height * 0.75), a.width, a.height)
    assert ImageChops.difference(a.crop(band), b.crop(band)).getbbox() is not None  # texto na faixa de baixo


@needs_ffmpeg
def test_subtitles_job_end_to_end_with_srt_export(me, no_ai, login, make_user, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    u = make_user(f"subs-job-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    url, _ = _mine_video(me2)
    r = c.post("/api/video/subtitles", json={"source_url": url, "style": "shorts", "position": "middle", "size": "small",
                                             "segments": [{"start": 0.5, "end": 2.0, "text": "frase curta"}]})
    assert r.status_code == 200, r.text
    asyncio.run(jobs._run(started[0], me2))
    assert started[0]["status"] == "done", started[0].get("error")
    assert abs(media.probe(storage.resolve(started[0]["output"], me2))["duration"] - 3.0) < 0.3
    srt_path = storage.resolve(started[0]["result"]["srt_url"], me2)
    assert "frase curta" in srt_path.read_text(encoding="utf-8")  # o .srt guarda o texto original
