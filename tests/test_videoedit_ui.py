"""Node Editar vídeo no canvas: paleta, endpoints, estimativa com confirmação acima de US$ 0,25, seletor de trecho
acessível, aviso de aproximação, sem inline e textos en/es."""
import re

from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_video_edit_node_is_in_video_palette_with_kind_and_script():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'videoedit'" in ws
    assert "videoedit: {\n    icon: 'video'" in src and "newVideoEdit(pos)" in src
    assert "videoedit: { label: 'Editar vídeo', icon: 'video' }" in src
    assert "if (n.type === 'videoedit') bindVideoEdit(el, n);" in src
    assert '<script src="/js/videoedit.js"></script>' in _text(STATIC / "studio.html")


def test_quote_comes_before_run_and_confirmation_gates_the_run():
    src = _text(JS / "videoedit.js")
    assert "'/api/video/edit/quote'" in src and "'/api/video/edit'" in src
    run = src.split("async function runVideoEdit")[1]
    assert run.index("quoteVideoEdit(n)") < run.index("api('/api/video/edit'")  # estimativa antes de gastar
    assert "if (q.confirm && !document.querySelector" in run and "return toast('Confirme o custo estimado para editar')" in run
    assert "return watchJob(n)" in run


def test_trim_is_an_accessible_group_with_labelled_inputs():
    src = _text(JS / "videoedit.js")
    assert 'role="group" aria-label="Trecho a editar"' in src
    assert 'aria-label="Início do trecho (segundos)"' in src and 'aria-label="Fim do trecho (segundos)"' in src
    assert "span > 10" in src  # limite de 10 s no front, espelho do servidor


def test_honest_approximation_note_is_shown_on_the_node():
    assert "Edição por quadros (aproximação): cada quadro é editado por IA e remontado. Pode haver tremidos." in _text(JS / "videoedit.js")


def test_no_inline_styles_in_the_video_edit_code():
    src = _text(JS / "videoedit.js")
    assert 'style="' not in src and "style='" not in src and ".style." not in src


def test_english_and_spanish_cover_the_video_edit_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Editar trecho", "Ver custo", "Manter o áudio original", "Início (s)", "Mudar a cor de um elemento"):
        assert f'"{label}"' in en or f"'{label}'" in en, label
    for label in ("Edit segment", "See cost", "Keep the original audio"):
        assert f'"{label}"' in es, label
