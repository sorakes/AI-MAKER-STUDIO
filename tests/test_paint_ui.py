"""Editores de desenho: um só caminho (sem upload de máscara/desenho), marcação sobre o preview, sem inline."""
from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_drawing_editors_do_not_upload_files_to_the_library():
    d = _text(JS / "direction.js")
    assert "uploadFile(new File([blob], `direcao_" not in d  # o desenho não vira arquivo em Meus arquivos
    assert "annotatedImage(640)" in d  # guardado no documento do node, pequeno
    assert "const diagram = activeLayers().length ? annotatedImage(640) : null;" in d


def test_mask_is_compact_before_it_goes_into_the_node():
    m = _text(JS / "mask.js")
    assert "512 / Math.max(W, H)" in m and "small.toDataURL('image/png')" in m


def test_overlay_is_drawn_on_every_node_preview_and_survives_reload():
    c = _text(JS / "canvas.js")
    assert "bindPaintOverlay(el, n);" in c
    o = _text(JS / "overlay.js")
    assert "requestAnimationFrame(() => drawPaintOverlay(el, n))" in o
    assert "'Região pintada'" in o and "'Limpar região'" in o and "data-act" in o
    assert "PAINT_MAGENTA = [217, 70, 239]" in o and "d.data[i + 3] = on ? 102 : 0" in o  # magenta 40%


def test_overlay_uses_the_same_framing_as_the_preview():
    o = _text(JS / "overlay.js")
    assert "Math.max(boxW / imgW, boxH / imgH)" in o  # object-fit: cover


def test_no_inline_styles_in_the_overlay_and_positioning_uses_data_st():
    o = _text(JS / "overlay.js")
    assert 'style="' not in o and "style='" not in o and ".style." not in o
    assert o.count("'data-st'") >= 3  # setAttribute('data-st', ...) é aplicado pelo styles.js


def test_overlay_script_is_loaded_on_the_studio_page():
    assert '<script src="/js/overlay.js"></script>' in _text(STATIC / "studio.html")
