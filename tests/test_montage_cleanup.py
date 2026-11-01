"""Falha do ffmpeg não deixa arquivo parcial em cuts/."""
import subprocess
from pathlib import Path

import pytest

from app import montage, storage


def _fake_ffmpeg_fail(args, **kwargs):
    Path(args[-1]).write_bytes(b"parcial")  # ffmpeg começou a escrever e morreu
    return subprocess.CompletedProcess(args, 1, stdout="", stderr="erro de codec")


def test_partial_cut_is_removed_when_ffmpeg_fails(make_user, monkeypatch, tmp_path):
    u = make_user("montagem@test.local")
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(montage, "probe", lambda p: {"duration": 5})
    monkeypatch.setattr(montage.subprocess, "run", _fake_ffmpeg_fail)
    with pytest.raises(RuntimeError, match="erro de codec"):
        montage.assemble_sequence(u["id"], [{"type": "clip", "path": clip}], "16:9", None)
    cuts = storage.user_dir(u["id"], "cuts")
    assert list(cuts.glob("corte_*.mp4")) == []
