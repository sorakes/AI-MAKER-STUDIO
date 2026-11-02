"""Listagem da biblioteca: vídeo sem miniatura (ex.: corte antigo) ganha o 1º quadro, sem 404 na tela."""
import shutil
import subprocess

import pytest

from app import media, storage

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")


@needs_ffmpeg
def test_listing_creates_the_missing_first_frame_of_an_old_video():
    d = storage.user_dir(9123, "cuts")
    v = d / "corte_legado.mp4"
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x90:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True, capture_output=True)
    poster = d / "corte_legado_first.png"
    poster.unlink(missing_ok=True)
    storage.list_files(9123)
    assert poster.exists()
