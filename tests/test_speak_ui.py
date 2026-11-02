"""Node Fala (personagem falando) no canvas: paleta, endpoint, aviso honesto, sem inline e textos en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_speak_node_is_in_palette_of_character_and_video_workspaces():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert ws.count("'speak'") == 3
    assert "speak: {" in src and "newSpeak(pos)" in src


def test_speak_node_calls_the_lipsync_endpoint_and_waits_on_the_job():
    src = _text(JS / "canvas.js")
    assert "'/api/video/lipsync'" in src
    assert "return watchJob(n)" in src.split("async function runSpeak")[1][:1800]


def test_honest_fallback_message_is_shown_in_the_node():
    src = _text(JS / "canvas.js")
    assert "sem lip sync real, animação simples" in src
    assert "Sem lip sync real hoje" in src


def test_audio_upload_is_focusable_and_uses_the_server_validation():
    src = _text(JS / "canvas.js")
    assert 'data-role="speak-audio"' in src and 'class="sr-only"' in src
    assert "cv-upload" in src.split("function speakBody")[1][:900]  # foco visível via .cv-upload:focus-within
    assert "uploadFile(file)" in src.split("function bindSpeak")[1][:900]


def test_no_inline_styles_in_the_new_code():
    assert not re.search(r"(?<=[\s<`])style=\"", _text(JS / "canvas.js"))


def test_english_and_spanish_cover_the_speak_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Enviar áudio", "Gerar fala", "Sem provedor de voz", "Texto da fala"):
        assert f"\"{label}\"" in en or f"'{label}'" in en, label
    for label in ("Upload audio", "Generate speech", "No voice provider", "Speech text"):
        assert f"\"{label}\"" in en or f"'{label}'" in en or f"\"{label}\"" in es, label
    assert "sin lip sync real, animación simple" in es
