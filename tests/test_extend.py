"""Estender vídeo e loop perfeito: grafos e textos (sem ffmpeg), ffmpeg real (skip sem ffmpeg) e provedor simulado."""
import asyncio
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, direction, extend, jobs, media, registry, storage, usage
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")

FAKE_I2V = {"id": 31, "kind": "video", "label": "Vídeo com quadro inicial", "provider": "openrouter", "model_id": "fake/i2v",
            "caps": {"i2v": True, "t2v": True, "durations": [3, 5, 8]}, "fields": {"api": "fake"}, "params": {},
            "cost": 0.05, "cost_unit": "second", "is_default": 1, "key_id": None, "key_name": "k", "enabled": 1}


def _uniq():
    return str(time.time_ns())


def _clip(path, seconds, audio=True, size="160x120"):
    """Clipe de teste real: vídeo com padrão, e trilha de áudio opcional."""
    cmd = [media._ff("ffmpeg"), "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else ["-an"]) + [str(path)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    return path


@pytest.fixture
def me(make_user):
    return auth.public_user(make_user(f"ext-{_uniq()}@test.local"))


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda *a, **k: None)
    monkeypatch.setattr(registry, "for_user", lambda *a, **k: [])
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


@pytest.fixture
def i2v_available(monkeypatch):
    monkeypatch.setattr(registry, "pick", lambda user, kind, model_id=None, need=None: FAKE_I2V if (need or {}).get("i2v") else None)
    monkeypatch.setattr(registry, "get", lambda mid: FAKE_I2V)

    async def aget(mid):
        return FAKE_I2V
    monkeypatch.setattr(registry, "aget", aget)
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [FAKE_I2V])
    monkeypatch.setattr(registry, "estimate", lambda m, seconds=5: 0.05 * seconds)
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _mine_video(me, seconds=2, audio=True, name=None):
    p = storage.user_dir(me["id"], "uploads") / (name or f"clipe-{_uniq()}.mp4")
    _clip(p, seconds, audio=audio)
    return storage.to_url(p), p


# ---------- sem ffmpeg ----------

def test_pingpong_graph_reverses_video_and_audio_when_present():
    g, maps = extend.loop_graph("pingpong", 2.0, 0, audio=True)
    assert "reverse" in g and "areverse" in g and "concat=n=2" in g
    assert maps == ["[v]", "[a]"]
    g2, maps2 = extend.loop_graph("pingpong", 2.0, 0, audio=False)
    assert "areverse" not in g2 and maps2 == ["[v]"]


def test_crossfade_graph_blends_the_tail_into_the_head():
    g, maps = extend.loop_graph("crossfade", 4.0, 1.0, audio=True)
    assert "xfade=transition=fade:duration=1.000" in g
    assert "trim=start=3.000:end=4.000" in g and "acrossfade=d=1.000" in g
    assert maps == ["[v]", "[a]"]


def test_extend_prompt_names_the_length_and_the_drawn_camera_route():
    cam = direction.Layer(color="camera", prompt="avança devagar", shapes=[
        direction.Shape(type="path", points=[[0.2, 0.5], [0.8, 0.5]])])
    req = extend.ExtendRequest(source_url="/files/1/x.mp4", seconds=8, direction_layers=[cam], prompt="luz da tarde")
    p = extend.extend_prompt(req)
    assert "8 seconds" in p and "avança devagar" in p and "camera route" in p and "luz da tarde" in p
    assert "first frame" in p and "No cuts" in p


def test_seconds_and_modes_are_validated():
    with pytest.raises(ValueError):
        extend.ExtendRequest(source_url="/f", seconds=4)
    with pytest.raises(ValueError):
        extend.LoopRequest(source_url="/f", mode="espelho")
    with pytest.raises(ValueError):
        extend.LoopRequest(source_url="/f", seconds=2.0)


def test_no_start_frame_model_is_the_honest_default(me, no_ai):
    assert extend.pick(me, "auto") is None
    assert "Loop" in extend.NO_START_MSG


# ---------- ffmpeg real ----------

@needs_ffmpeg
def test_pingpong_loop_lasts_twice_the_clip_and_keeps_sound(me, tmp_path):
    src = _clip(tmp_path / "a.mp4", 2)
    out = extend.apply_loop({"kind": "loop", "mode": "pingpong", "seconds": 1.0}, src, tmp_path / "pp.mp4")
    assert abs(media.probe(out)["duration"] - 4.0) < 0.3
    assert extend.has_audio(out)


@needs_ffmpeg
def test_crossfade_loop_is_the_clip_minus_the_fade(me, tmp_path):
    src = _clip(tmp_path / "b.mp4", 3, audio=False)
    out = extend.apply_loop({"kind": "loop", "mode": "crossfade", "seconds": 1.0}, src, tmp_path / "cf.mp4")
    assert abs(media.probe(out)["duration"] - 2.0) < 0.3
    assert not extend.has_audio(out)


@needs_ffmpeg
def test_crossfade_longer_than_half_the_clip_is_refused(tmp_path):
    src = _clip(tmp_path / "c.mp4", 1)
    with pytest.raises(ValueError, match="metade"):
        extend.apply_loop({"kind": "loop", "mode": "crossfade", "seconds": 1.5}, src, tmp_path / "x.mp4")


@needs_ffmpeg
def test_loop_endpoint_runs_end_to_end(me, no_ai, login, make_user, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    u = make_user(f"loop-api-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    url, _ = _mine_video(me2, 2)
    r = c.post("/api/video/loop", json={"source_url": url, "mode": "pingpong"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    asyncio.run(jobs._run(started[0], me2))
    assert started[0]["status"] == "done", started[0].get("error")
    assert abs(media.probe(storage.resolve(started[0]["output"], me2))["duration"] - 4.0) < 0.3


@needs_ffmpeg
def test_extend_uses_the_last_frame_and_joins_the_continuation(me, i2v_available, monkeypatch, login, make_user):
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    continuation = {}

    async def fake_or_video(job, st, m, i, n, prompt, spec, prev_image, ref_urls):
        # provedor simulado: devolve uma continuação real de 3 s (ffmpeg), sem chamar API nenhuma
        out = _clip(storage.user_dir(me["id"], "renders") / f"cont-{_uniq()}.mp4", 3, audio=False)
        continuation["prev"] = prev_image
        return out.read_bytes()
    monkeypatch.setattr(jobs, "_or_video", fake_or_video)
    u = make_user(f"ext-api-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me2 = auth.public_user(u)
    url, _ = _mine_video(me2, 2)
    r = c.post("/api/video/extend", json={"source_url": url, "seconds": 3, "transition": "none",
                                          "direction_layers": [{"color": "camera", "label": "CÂMERA", "prompt": "zoom lento",
                                                                "shapes": [{"type": "path", "points": [[0.2, 0.5], [0.8, 0.5]]}]}]})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "ai"
    job = started[0]
    assert job["spec"]["start_image_url"].endswith("_last.png")
    assert "zoom lento" in job["spec"]["prompt"]
    asyncio.run(jobs._run(job, me2))
    assert job["status"] == "done", job.get("error")
    assert continuation["prev"].endswith("_last.png")  # a continuação partiu do último frame
    assert abs(media.probe(storage.resolve(job["output"], me2))["duration"] - 5.0) < 0.4  # 2 s originais + 3 s novos


@needs_ffmpeg
def test_extend_without_start_frame_model_offers_only_the_loop(me, no_ai, login, make_user):
    u = make_user(f"ext-msg-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    url, _ = _mine_video(auth.public_user(u), 2)
    r = c.post("/api/video/extend", json={"source_url": url, "seconds": 3})
    assert r.status_code == 400 and "Loop" in r.json()["detail"]


@needs_ffmpeg
def test_source_must_be_a_real_video_and_belong_to_the_user(login, make_user, me, no_ai):
    u = make_user(f"ext-val-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    img = storage.user_dir(u["id"], "uploads") / "nao-video.png"
    img.write_bytes(b"not a video")
    assert c.post("/api/video/loop", json={"source_url": storage.to_url(img)}).status_code == 400
    other = auth.public_user(make_user(f"outro-ext-{_uniq()}@test.local"))
    url, _ = _mine_video(other, 1)
    assert c.post("/api/video/loop", json={"source_url": url}).status_code == 403
