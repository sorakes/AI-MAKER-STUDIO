"""Node Estender/loop no canvas: paleta, endpoints, direção reaproveitada, aviso sem modelo, sem inline e textos en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(name):
    return (JS / name).read_text(encoding="utf-8")


def test_extend_node_is_in_the_video_palette():
    src = _text("canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'extend'" in ws
    assert "extend: {" in src and "newExtend(pos)" in src


def test_extend_node_calls_both_endpoints():
    src = _text("canvas.js")
    assert "'/api/video/extend'" in src and "'/api/video/loop'" in src
    assert "return watchJob(n)" in src.split("function startExtendJob")[1][:400]


def test_camera_direction_is_reused_on_the_extend_node():
    src = _text("canvas.js")
    body = src.split("function extendBody")[1][:1500]
    assert "directionCard(n)" in body  # mesmo card de câmera dos planos
    assert "bindDirectionCard(el, n)" in src.split("function bindExtend")[1][:600]


def test_no_start_frame_message_leaves_only_the_loop():
    src = _text("canvas.js")
    assert "Use o Loop" in src and "n.noStartModel = true" in src
    assert "ext && n.noStartModel" in src  # o botão de estender fica desabilitado


def test_no_inline_styles_in_the_new_code():
    assert not re.search(r"(?<=[\s<`])style=\"", _text("canvas.js"))


def test_english_and_spanish_cover_the_extend_labels():
    en = _text("i18n-en.js")
    es = _text("i18n-es.js")
    for label in ("Duração da continuação", "Crossfade final → início", "Ida e volta"):
        assert f"\"{label}\"" in en or f"'{label}'" in en, label
    for label in ("Continuation length", "Ping-pong", "Crossfade end → start"):
        assert f"\"{label}\"" in en or f"'{label}'" in en, label
    assert "Duración de la continuación" in es or "Continuation length" in es
