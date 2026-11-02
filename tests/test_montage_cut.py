"""Corte seco na montagem com ffmpeg real: a duração total é a soma dos trechos (sem perder quadros nos cortes)."""
import shutil
import subprocess

import pytest

from app import media, montage, storage

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


def _clip(path, seconds):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=320x180:rate=24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_cut_only_sequence_keeps_the_sum_of_the_clips(tmp_path):
    a, b, c = (_clip(tmp_path / f"{n}.mp4", 1.0) for n in "abc")
    out = montage.assemble_sequence(1, [{"type": "clip", "path": a}, {"type": "clip", "path": b},
                                        {"type": "clip", "path": c}], "16:9")
    try:
        assert abs(media.probe(out)["duration"] - 3.0) < 0.05  # antes: 2 cortes tiravam ~0,1 s
        assert out.with_name(f"{out.stem}_first.png").exists()  # miniatura: a biblioteca não pede um 404
    finally:
        out.unlink(missing_ok=True)
