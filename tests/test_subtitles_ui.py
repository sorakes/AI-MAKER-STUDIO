"""Node Legendas no canvas: paleta, endpoints, editor acessível, sem inline e textos en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def _subs_section():
    src = _text(JS / "canvas.js")
    start = src.index("// ---------------- Legendas")
    return src[start:src.index("// ----------------", start + 10)]  # até a próxima seção


def test_subs_node_is_in_palette_of_video_workspace_and_has_kind():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'subs'" in ws
    assert "subs: {" in src and "newSubs(pos)" in src
    assert "subs: { label: 'Legendas', icon: 'film' }" in src


def test_subs_node_calls_transcribe_and_subtitles_endpoints_and_waits_on_the_job():
    sec = _subs_section()
    assert "'/api/video/transcribe'" in sec and "'/api/video/subtitles'" in sec
    assert "await watchJob(n)" in sec and "return watchJob(n)" in sec
    assert "result: j.result || null" in _text(JS / "canvas.js")  # resultado (segmentos/.srt) chega ao node


def test_subs_editor_controls_have_accessible_names():
    sec = _subs_section()
    for label in ("Início (segundos)", "Fim (segundos)", "Texto da legenda", "Remover legenda", "Legendas"):
        assert f'aria-label="{label}"' in sec or f'"{label}"' in sec, label
    assert "data-role=\"subs-import\"" in sec and "accept=\".srt,.vtt\"" in sec
    assert "class=\"sr-only\"" in sec  # o input de arquivo fica focável pelo label com :focus-within


def test_no_inline_styles_in_the_subs_code():
    assert "style=\"" not in _subs_section()
    assert "style=\"" not in _text(JS / "canvas.js").split("// ---------------- Legendas")[1].split("// ----------------")[0]


def test_subs_parser_accepts_srt_and_vtt_shapes():
    sec = _subs_section()
    assert "function parseSubsText(text)" in sec
    assert "replace(',', '.')" in sec  # vírgula decimal do SRT
    assert "Number.isFinite(start)" in sec  # linhas inválidas são descartadas


def test_english_and_spanish_cover_the_subs_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Legendas", "Transcrever áudio", "Gerar legendas", "Adicionar legenda", "Karaokê (palavra a palavra)",
                  "Traduzir legendas", "Remover legenda", "Baixar .srt"):
        assert f'"{label}"' in en, label
    for label in ("Transcribe audio", "Generate subtitles", "Add subtitle", "Karaoke (word by word)",
                  "Translate subtitles", "Remove subtitle", "Download .srt"):
        assert f'"{label}"' in es, label
