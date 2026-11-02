"""Node Performance e Fala com fluxo único: paleta, endpoints, estimativa com confirmação, avisos honestos,
sem inline e textos en/es."""
import re

from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_performance_node_is_in_video_palette_with_kind_and_script():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'performance'" in ws
    assert "performance: {\n    icon: 'user'" in src and "newPerformance(pos)" in src
    assert "performance: { label: 'Performance', icon: 'user' }" in src
    assert "if (n.type === 'performance') bindPerformance(el, n);" in src
    assert '<script src="/js/performance.js"></script>' in _text(STATIC / "studio.html")


def test_quote_comes_before_generate_and_confirmation_gates_it():
    src = _text(JS / "performance.js")
    assert "'/api/video/performance/quote'" in src and "'/api/video/performance'" in src
    run = src.split("async function runPerformance")[1]
    assert run.index("quotePerformance(n)") < run.index("api('/api/video/performance'")
    assert "return toast('Confirme o custo estimado para gerar')" in run


def test_fala_has_one_send_path_real_model_first_then_tts_fallback():
    src = _text(JS / "canvas.js")
    run = src.split("async function runSpeak")[1].split("async function startSpeakJob")[0]
    assert run.index("startSpeakJob(n, src, null)") < run.index("speechForSpeak(n)")  # modelo real antes da voz
    assert "if (!/Sem lip sync real/.test(err.message)) throw err;" in run  # só cai no fallback nesse caso
    assert "return startSpeakJob(n, src, n.audio?.url || null);" in run
    helper = src.split("async function startSpeakJob")[1][:900]
    assert "api('/api/video/lipsync'" in helper and "return watchJob(n)" in helper


def test_honest_notes_are_on_the_node():
    src = _text(JS / "performance.js")
    assert "sincronizam com um áudio enviado" in src and "só define a duração" in src
    assert "A imagem é ajustada ao formato escolhido" in src
    assert "perf-limit" in src
    assert "Modelo real: fala o texto com voz própria" in _text(JS / "canvas.js")


def test_no_inline_styles_in_the_performance_code():
    src = _text(JS / "performance.js")
    assert 'style="' not in src and "style='" not in src and ".style." not in src


def test_english_and_spanish_cover_the_performance_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Gerar performance", "Texto da fala", "Expressão e movimento (opcional)"):
        assert f'"{label}"' in en or f"'{label}'" in en, label
    for label in ("Generate performance", "Speech text"):
        assert f'"{label}"' in en or f'"{label}"' in es, label
