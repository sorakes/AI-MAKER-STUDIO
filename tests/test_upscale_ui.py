"""Node Upscale no canvas: paleta, endpoint, sliders com rótulos e strings en/es."""
import re

from app import config

JS = config.STATIC_DIR / "js"


def _text(name):
    return (JS / name).read_text(encoding="utf-8")


def test_upscale_is_in_palette_of_the_workspaces_that_generate_images():
    src = _text("canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert ws.count("'upscale'") == 3  # video, character e product
    assert "upscale: {" in src and "newUpscale(pos)" in src


def test_upscale_calls_the_endpoint_and_waits_on_the_job():
    src = _text("canvas.js")
    assert "'/api/image/upscale'" in src
    assert "return watchJob(n)" in src.split("async function runUpscale")[1][:1200]


def test_sliders_have_labels_and_no_inline_styles():
    src = _text("canvas.js")
    assert 'aria-label="${label}"' in src and 'type="range"' in src
    assert not re.search(r'(?<=[\s<`])style="', src)


def test_english_and_spanish_cover_the_new_labels():
    en = _text("i18n-en.js")
    es = _text("i18n-es.js")
    for label in ("Criatividade", "Semelhança", "Fator", "Ampliar", "Upscale criativo"):
        assert f"'{label}'" in en or f'"{label}"' in en, label
    for label in ("Creativity", "Similarity", "Enlarge"):
        assert f'"{label}"' in es or f"'{label}'" in es, label
