"""Visualizador em modal: um só componente, disparado pelo duplo clique em mídia (painel, inventário, nodes),
com Esc, setas, zoom, foco de volta ao elemento de origem, sem inline style, e com traduções."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_one_modal_dialog_with_aria_and_no_inline_style():
    html = _read("static/studio.html")
    block = html.split('<dialog id="viewerDialog"', 1)[1].split("</dialog>", 1)[0]
    assert 'role="dialog" aria-modal="true"' in block
    assert 'aria-labelledby="viewerName"' in block
    assert "style=" not in block  # CSP: nada de atributo style na marcação
    assert html.count('id="viewerDialog"') == 1


def test_viewer_script_loads_before_the_canvas_and_inventory():
    html = _read("static/studio.html")
    assert html.index("/js/viewer.js") < html.index("/js/canvas.js") < html.index("/js/inventory.js")


def test_viewer_handles_escape_arrows_zoom_and_focus_return():
    js = _read("static/js/viewer.js")
    assert "dlg.addEventListener('close'" in js  # Esc, botão e clique fora caem aqui
    assert "back.focus()" in js and "back?.isConnected" in js  # foco volta ao que abriu
    assert "e.key === 'ArrowLeft'" in js and "e.key === 'ArrowRight'" in js
    assert "addEventListener('wheel'" in js and "dblclick" in js  # roda e duplo clique alternam ajustar/100%
    assert "setAttribute('tabindex', '-1')" in js  # origem não focável ganha foco por script
    assert "el.autoplay = true" in js and "el.muted = true" in js  # autoplay só mudo, opcional
    assert "el.loop = $('#viewerLoop').checked" in js


def test_viewer_never_builds_markup_from_names_or_urls():
    js = _read("static/js/viewer.js")
    assert "innerHTML =" not in js and "insertAdjacentHTML" not in js  # nome e URL entram como texto
    assert ".style.cssText" not in js and "setAttribute('style'" not in js


def test_every_trigger_opens_the_same_viewer():
    viewer = _read("static/js/viewer.js")
    assert "document.addEventListener('dblclick'" in viewer
    assert ".cv-lib-item" in viewer and ".cv-preview img:not(.ghost), .cv-preview video" in viewer
    assert "[data-del]" in viewer  # o botão de apagar não abre o visualizador
    inventory = _read("static/js/inventory.js")
    assert "viewerOpen(vis, f.url, el)" in inventory
    assert "window.open(f.url, '_blank')" not in inventory  # o duplo clique agora abre o modal
    canvas = _read("static/js/canvas.js")
    assert "LIBRARY_ITEMS = items;" in canvas  # o painel publica a lista para ← →


def test_title_rename_still_uses_its_own_double_click():
    canvas = _read("static/js/canvas.js")
    assert "title.addEventListener('dblclick', () => { title.contentEditable = 'true'; title.focus(); });" in canvas
    assert "el.addEventListener('dblclick', () => renameTab(el.dataset.tab));" in canvas


def test_css_keeps_the_viewer_inside_any_screen():
    css = _read("static/css/studio.css")
    block = css.split(".viewer-dialog {", 1)[1]
    assert "max-width: 96vw" in block and "max-height: 92vh" in block
    assert "@media (max-width: 640px)" in css and "max-width: 100vw" in css


def test_viewer_strings_are_translated_to_en_and_es():
    en = _read("static/js/i18n-en.js")
    es = _read("static/js/i18n-es.js")
    for key in ["Anterior", "Próximo", "Ampliar", "Reproduzir sozinho (mudo)", "Abrir em nova aba", "Fechar"]:
        assert f'"{key}"' in en, key
    # o dicionário em espanhol é JSON sem espaços após os dois-pontos
    assert '"Previous":"Anterior"' in es and '"Close":"Cerrar"' in es and '"Open in new tab":"Abrir en nueva pestaña"' in es
    assert re.search(r'"Play automatically \(muted\)":"Reproducir solo \(silenciado\)"', es)
