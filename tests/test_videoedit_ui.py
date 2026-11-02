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


def test_node_only_shows_the_tokens_it_will_use_without_confirmation():
    src = _text(JS / "videoedit.js")
    assert "'/api/video/edit/quote'" in src and "'/api/video/edit'" in src
    run = src.split("async function runVideoEdit")[1]
    assert run.index("quoteVideoEdit(n)") < run.index("api('/api/video/edit'")  # estimativa antes de gastar
    assert "vedit-confirm" not in src and "Confirmo o custo" not in src and "q.confirm" not in src  # sem confirmação
    assert "Ver custo" not in src and "vedit-quote" not in src  # sem botão: a estimativa aparece sozinha
    assert "Vai usar cerca de ${fmtTok(q.tokens)}." in src and "veditAutoQuote(n, src, p)" in src
    assert "return watchJob(n)" in run


def test_trim_is_an_accessible_group_with_labelled_inputs():
    src = _text(JS / "videoedit.js")
    assert 'role="group" aria-label="Trecho a editar"' in src
    assert 'aria-label="Início do trecho (segundos)"' in src and 'aria-label="Fim do trecho (segundos)"' in src
    assert "span > maxSeconds" in src and "native_max || 120" in src  # limite no front, espelho do servidor


def test_node_says_the_model_receives_the_video_and_offers_no_frames_mode():
    src = _text(JS / "videoedit.js")
    assert "O modelo recebe o vídeo" in src
    assert "Por quadros" not in src and "opt('frames'" not in src


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
