"""Node Expandir/editar região no canvas e editor de máscara: paleta, endpoints, dialog, sem inline e textos en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_edit_node_is_in_palette_of_all_image_workspaces():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert ws.count("'edit'") == 3
    assert "edit: {" in src and "newEdit(pos)" in src


def test_edit_node_calls_both_endpoints_and_waits_on_the_job():
    src = _text(JS / "canvas.js")
    assert "'/api/image/outpaint'" in src and "'/api/image/inpaint'" in src
    assert "return watchJob(n)" in src.split("async function runEdit")[1][:2000]


def test_mask_dialog_is_wired_in_the_studio_page():
    html = _text(config.STATIC_DIR / "studio.html")
    for el in ("maskDialog", "maskCanvas", "maskImg", "maskBrush", "maskEraser", "maskSize", "maskUndo", "maskSave"):
        assert f'id="{el}"' in html, el
    assert html.index("/js/direction.js") < html.index("/js/mask.js")


def test_mask_editor_exports_black_and_white_and_has_undo():
    mask = _text(JS / "mask.js")
    assert "function maskDataUrl" in mask and "d[i] > 127" in mask  # pintura lida pelo alfa
    assert "function fillEnclosed" in mask and "fillEnclosed(bin, W, H)" in mask  # contorno fechado vira região
    assert "function undoMask" in mask and "destination-out" in mask  # desfazer e borracha
    assert "lineWidth = mk.size" in mask or "lineWidth = mk.size * scale" in mask  # tamanho ajustável


def test_no_inline_styles_in_the_new_code():
    for path in (JS / "canvas.js", JS / "mask.js", config.STATIC_DIR / "studio.html"):
        assert not re.search(r"(?<=[\s<`])style=\"", _text(path)), path.name


def test_english_and_spanish_cover_the_edit_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Pincel", "Borracha", "Tamanho do pincel", "Desfazer", "Pintar região", "Centro"):
        assert f"\"{label}\"" in en or f"'{label}'" in en, label
    for label in ("Brush", "Eraser", "Brush size", "Paint region", "Centre"):
        assert f"\"{label}\"" in es or f"'{label}'" in es, label
