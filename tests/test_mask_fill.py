"""Máscara do inpaint: contorno fechado vira região cheia; traço aberto e rabisco cheio não mudam."""
import time

from PIL import Image, ImageDraw

from app import outpaint as o


def _ring(size, box, width, gap=None):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse(box, outline=255, width=width)
    if gap:  # corta um pedaço do traço (falha no contorno)
        ImageDraw.Draw(m).rectangle(gap, fill=0)
    return m


def test_closed_ring_becomes_filled_disk():
    ring = _ring((400, 400), (80, 80, 320, 320), 12)
    assert ring.getpixel((200, 200)) == 0  # interior vazio antes
    out = o.fill_enclosed(ring)
    assert out.size == (400, 400)
    assert out.getpixel((200, 200)) == 255
    filled = sum(1 for v in out.getdata() if v)
    disk = 3.14159 * 120 * 120  # área do círculo de raio 120
    assert abs(filled - disk) / disk < 0.08  # o disco inteiro, não só o traço
    assert out.getpixel((5, 5)) == 0  # fora do contorno nada entra


def test_ring_with_small_gap_still_fills():
    ring = _ring((400, 400), (80, 80, 320, 320), 12, gap=(197, 70, 203, 90))  # falha de ~7 px no topo
    out = o.fill_enclosed(ring)
    assert out.getpixel((200, 200)) == 255


def test_open_stroke_does_not_fill():
    m = Image.new("L", (400, 400), 0)
    ImageDraw.Draw(m).arc((80, 80, 320, 320), start=0, end=270, fill=255, width=12)  # 'C' aberto
    out = o.fill_enclosed(m)
    assert out.getpixel((200, 200)) == 0
    assert list(out.getdata()) == list(m.getdata())


def test_solid_scribble_is_unchanged():
    m = Image.new("L", (400, 400), 0)
    ImageDraw.Draw(m).rectangle((100, 100, 300, 300), fill=255)  # rabisco cheio: já é região
    assert list(o.fill_enclosed(m).getdata()) == list(m.getdata())


def test_ring_touching_the_frame_still_fills_the_inside():
    ring = _ring((400, 400), (0, 0, 399, 399), 12)  # encosta nas quatro bordas
    out = o.fill_enclosed(ring)
    assert out.getpixel((200, 200)) == 255
    assert out.getpixel((5, 5)) == 0  # o canto fora do círculo toca a borda: é 'de fora' e não é preenchido


def test_empty_mask_is_returned_as_is():
    m = Image.new("L", (64, 64), 0)
    assert o.fill_enclosed(m).getbbox() is None


def test_fill_is_fast_at_5120_by_2880():
    ring = _ring((5120, 2880), (1000, 300, 4100, 2580), 40)
    t0 = time.perf_counter()
    out = o.fill_enclosed(ring)
    elapsed = time.perf_counter() - t0
    assert out.getpixel((2500, 1440)) == 255
    assert elapsed < 1.0, f"{elapsed:.2f}s"
