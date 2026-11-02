"""Node Áudio no canvas: paleta, endpoints, abas acessíveis, efeito rotulado, Fala com voz (TTS), trilha na Montagem,
sem inline e textos en/es."""
import re

from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_audio_node_is_in_video_palette_and_has_kind_and_script():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'audio'" in ws
    assert "audio: {\n    icon: 'music'" in src and "newAudio(pos)" in src
    assert "audio: { label: 'Áudio', icon: 'music' }" in src
    assert "if (n.type === 'audio') bindAudio(el, n);" in src and "audio: audioBody" in src
    assert '<script src="/js/audio.js"></script>' in _text(STATIC / "studio.html")


def test_each_tab_calls_its_own_endpoint_and_waits_on_the_job():
    src = _text(JS / "audio.js")
    assert "'/api/audio/speech'" in src and "'/api/audio/music'" in src and "'/api/audio/sfx'" in src
    assert "return watchJob(n)" in src.split("async function runAudio")[1][:1500]


def test_tabs_are_accessible_tablist_with_arrow_keys():
    src = _text(JS / "audio.js")
    assert 'role="tablist"' in src and 'role="tab"' in src and 'role="tabpanel"' in src
    assert "aria-selected=" in src and "aria-controls=" in src and "tabindex=" in src
    assert "ArrowRight" in src and "ArrowLeft" in src  # teclado entre as abas
    assert 'aria-label="Tipo de áudio"' in src


def test_sound_effect_is_labelled_as_synthesized_not_recorded():
    src = _text(JS / "audio.js")
    assert "Efeito sintetizado no servidor (ffmpeg). Não é gravação real." in src


def test_voice_text_becomes_audio_before_speak_animates():
    canvas = _text(JS / "canvas.js")
    run = canvas.split("async function runSpeak")[1]
    assert run.index("speechForSpeak(n)") < run.index("/api/video/lipsync")  # TTS antes da animação
    assert "return watchJob(n)" in run[:1800]
    assert '<select data-k="voice">' in canvas  # voz da Fala habilitada (antes: "Sem provedor de voz", desabilitada)


def test_montage_uses_a_linked_audio_track():
    canvas = _text(JS / "canvas.js")
    assert "audio_url: n.audio?.url || incomingAudioUrl(n)" in canvas
    assert "const src = cv.edges.filter((e) => e.to === n.id)" in _text(JS / "audio.js")


def test_preview_plays_audio_with_native_player():
    assert "out.kind === 'audio' ? `<audio src=" in _text(JS / "canvas.js")


def test_no_inline_styles_in_audio_code():
    src = _text(JS / "audio.js")
    assert 'style="' not in src and "style='" not in src and ".style." not in src


def test_english_and_spanish_cover_the_audio_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Gerar voz", "Gerar música", "Gerar efeito", "Velocidade", "Tipo de áudio", "Chuva", "Misterioso"):
        assert f'"{label}"' in en, label
    for label in ("Generate voice", "Generate music", "Generate sound effect"):
        assert f'"{label}"' in es, label
