"""Selo BETA nos recursos em beta (Áudio, Fala, Performance e o que é áudio): um componente, texto real, i18n."""
import re

from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(p):
    return p.read_text(encoding="utf-8")


def test_one_beta_component_with_real_text_and_accessible_name():
    c = _text(JS / "common.js")
    assert "const betaTag = () =>" in c
    assert 'class="beta-tag" role="note" aria-label="Recurso em beta"' in c
    assert ">BETA</span>" in c and 'title="Recurso em beta: pode mudar ou falhar"' in c
    assert ".beta-tag {" in _text(STATIC / "css" / "studio.css")


def test_palette_entries_of_beta_features_carry_the_badge():
    src = _text(JS / "canvas.js")
    for node in ("speak", "audio", "performance"):
        block = src.split(f"  {node}: {{")[1][:300]
        assert "beta: true" in block, node
    assert "${p.beta ? betaTag() : ''}" in src


def test_node_headers_of_beta_features_carry_the_badge():
    src = _text(JS / "canvas.js")
    assert re.search(r"const BETA_TYPES = \['speak', 'audio', 'performance'\];", src)
    assert "${BETA_TYPES.includes(n.type) ? betaTag() : ''}" in src


def test_fala_voice_selector_and_generate_button_carry_the_badge():
    src = _text(JS / "canvas.js")
    assert "field(`Voz ${betaTag()}`" in src
    assert "'Gerar fala'} ${betaTag()}</button>" in src


def test_audio_tabs_and_generate_buttons_carry_the_badge():
    a = _text(JS / "audio.js")
    assert "aria-label=\"Tipo de áudio\">${tabs}</div>${betaTag()}" in a
    assert "go} ${betaTag()}</button>" in a


def test_admin_audio_capability_is_marked_beta():
    assert "t.push('áudio (beta)')" in _text(JS / "admin.js")


def test_beta_texts_are_translated():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    assert '"Recurso em beta"' in en and '"Recurso em beta: pode mudar ou falhar"' in en
    assert '"Beta feature"' in es and '"Beta feature: it may change or fail"' in es
