"""Node Relight/Estilo no canvas e editor de direção da luz: paleta, endpoints, portas, sem inline e textos en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(name):
    return (JS / name).read_text(encoding="utf-8")


def test_relight_is_in_palette_of_all_image_workspaces():
    src = _text("canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert ws.count("'relight'") == 3
    assert "relight: {" in src and "newRelight(pos)" in src


def test_relight_calls_both_endpoints():
    src = _text("canvas.js")
    assert "'/api/image/relight'" in src and "'/api/image/style-transfer'" in src
    assert "return watchJob(n)" in src.split("async function runRelight")[1][:1600]


def test_style_input_is_a_separate_port_not_the_main_input():
    src = _text("canvas.js")
    assert "data-port=\"style\"" in src
    assert "e.port !== 'ref' && e.port !== 'style'" in src  # incoming() ignora a entrada S


def test_light_editor_mode_exists_and_saves_one_arrow():
    d = _text("direction.js")
    assert "function openLight(n)" in d and "function saveLight()" in d
    assert "dz.mode === 'light') l.shapes = [{ type: 'path', points: sh.points }]" in d
    assert "const arrowColor = (c) => c === 'camera' || c === 'light'" in d


def test_no_inline_styles_in_the_new_code():
    for name in ("canvas.js", "direction.js"):
        assert not re.search(r"(?<=[\s<`])style=\"", _text(name)), name


def test_english_and_spanish_cover_the_relight_labels():
    en = _text("i18n-en.js")
    es = _text("i18n-es.js")
    for label in ("Luz dourada", "Estúdio", "Neon", "Contraluz", "Intensidade", "Força", "Usar esta luz"):
        assert f"\"{label}\"" in en or f"'{label}'" in en, label
    for label in ("Golden light", "Studio", "Backlight", "Intensity", "Strength", "Use this light"):
        assert f"\"{label}\"" in es or f"'{label}'" in es, label
