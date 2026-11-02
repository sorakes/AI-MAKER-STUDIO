"""Quadro inicial da Performance: a imagem entra inteira no formato pedido, sem cortar o rosto."""
import pytest
from PIL import Image

from app import performance


@pytest.mark.parametrize("aspect", ["16:9", "9:16", "1:1"])
def test_fit_frame_gives_the_requested_size(tmp_path, aspect):
    src = tmp_path / "s.png"
    Image.new("RGB", (640, 640), (200, 60, 60)).save(src)
    out = performance.fit_frame(src, aspect, tmp_path / "o.png")
    assert Image.open(out).size == performance.FRAME_SIZES[aspect]


def test_fit_frame_keeps_the_whole_subject_centered(tmp_path):
    src = tmp_path / "s.png"
    img = Image.new("RGB", (640, 640), (200, 30, 30))  # fundo vermelho
    img.paste((255, 255, 255), (220, 220, 420, 420))     # sujeito branco no centro
    img.save(src)
    out = Image.open(performance.fit_frame(src, "16:9", tmp_path / "o.png")).convert("RGB")
    w, h = out.size
    assert out.getpixel((w // 2, h // 2)) == (255, 255, 255)  # centro: o sujeito inteiro
    top = out.getpixel((w // 2, 5))
    assert top[0] > 120 and top[1] < 100  # topo: fundo vermelho desfocado, sem cortar o sujeito


def test_needs_fit_only_when_the_shape_differs():
    assert performance.needs_fit((640, 640), "16:9") is True
    assert performance.needs_fit((1280, 720), "16:9") is False
    assert performance.needs_fit((854, 480), "16:9") is False
