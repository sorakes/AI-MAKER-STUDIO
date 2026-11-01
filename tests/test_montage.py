"""Montagem com ffmpeg: grafo de filtros (cortes, transições, trilha) e caminhos de erro, sem ffmpeg real."""
import subprocess
from pathlib import Path

import pytest

from app import montage, storage


@pytest.fixture
def clips(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    return a, b


@pytest.fixture
def ff(monkeypatch):
    """Substitui probe e subprocess.run: registra os argumentos e 'gera' o arquivo de saída."""
    calls = []
    monkeypatch.setattr(montage, "probe", lambda p: {"duration": 4.0})

    def run(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(montage.subprocess, "run", run)
    return calls


def _filter(args):
    return args[args.index("-filter_complex") + 1]


def test_two_clips_with_dissolve_build_xfade_chain(make_user, clips, ff):
    u = make_user("mont-a@test.local")
    a, b = clips
    out = montage.assemble_sequence(u["id"], [
        {"type": "clip", "path": a},
        {"type": "transition", "kind": "dissolve", "duration": 0.6},
        {"type": "clip", "path": b},
    ], "16:9", None)
    f = _filter(ff[0])
    assert "xfade=transition=fade:duration=0.600" in f
    assert "scale=1280:720" in f  # 16:9
    assert str(out).endswith(".mp4") and out.parent.name == "cuts"
    assert out.exists()


def test_transition_kind_maps_to_ffmpeg_transition(make_user, clips, ff):
    u = make_user("mont-b@test.local")
    a, b = clips
    montage.assemble_sequence(u["id"], [{"type": "clip", "path": a}, {"type": "transition", "kind": "iris"},
                                        {"type": "clip", "path": b}], "9:16", None)
    f = _filter(ff[0])
    assert "transition=circleopen" in f and "scale=720:1280" in f


def test_clip_trim_limits_are_applied(make_user, clips, ff):
    u = make_user("mont-c@test.local")
    a, _ = clips
    montage.assemble_sequence(u["id"], [{"type": "clip", "path": a, "start": 1.0, "end": 3.0}], "1:1", None)
    f = _filter(ff[0])
    assert "trim=start=1.000:end=3.000" in f and "scale=960:960" in f


def test_audio_track_is_mapped_and_trimmed(make_user, clips, ff, tmp_path):
    u = make_user("mont-d@test.local")
    a, b = clips
    audio = tmp_path / "trilha.mp3"
    audio.write_bytes(b"m")
    montage.assemble_sequence(u["id"], [{"type": "clip", "path": a}, {"type": "clip", "path": b}], "16:9", audio)
    args = ff[0]
    assert str(audio) in args
    assert "-map" in args and "aac" in args and "apad" in args


def test_empty_sequence_is_rejected(make_user, ff):
    u = make_user("mont-e@test.local")
    with pytest.raises(ValueError, match="nenhum vídeo"):
        montage.assemble_sequence(u["id"], [{"type": "transition", "kind": "cut"}], "16:9", None)
    assert ff == []  # nem chega a chamar o ffmpeg


def test_failure_removes_partial_output(make_user, clips, monkeypatch):
    u = make_user("mont-f@test.local")
    a, _ = clips
    monkeypatch.setattr(montage, "probe", lambda p: {"duration": 4.0})

    def run(args, **kwargs):
        Path(args[-1]).write_bytes(b"parcial")
        raise subprocess.TimeoutExpired(args, 900)

    monkeypatch.setattr(montage.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        montage.assemble_sequence(u["id"], [{"type": "clip", "path": a}], "16:9", None)
    assert list(storage.user_dir(u["id"], "cuts").glob("corte_*.mp4")) == []
