"""Dica do modo inpaint e 'Alvo identificado': texto no código, traduções em en e es, e só no modo inpaint."""
import json
import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "static" / "js"
PT_TIP = ('Dica: funciona melhor com objetos simples e isolados. Para remover algo fino ou sobreposto, pinte também um '
          'pedaço do fundo ao redor e descreva o que deve aparecer no lugar (ex.: "céu desfocado").')
EN_TIP = ('Tip: works best with simple, isolated objects. To remove something thin or overlapping, also paint a bit of '
          'the background around it and describe what should appear in its place (e.g.: "blurred sky").')
ES_TIP = ('Consejo: funciona mejor con objetos simples y aislados. Para quitar algo fino o superpuesto, pinta también un '
          'poco del fondo alrededor y describe lo que debe aparecer en su lugar (ej.: "cielo desenfocado").')


def _read(name):
    return (JS / name).read_text(encoding="utf-8")


def _q(s):
    return json.dumps(s, ensure_ascii=False)


def test_tip_is_in_the_inpaint_branch_only():
    src = _read("canvas.js")
    assert "const INPAINT_TIP = '" + PT_TIP + "';" in src
    # a dica só aparece no modo inpaint (o ramo outpaint não a mostra)
    assert "${outpaint ? '' : `<div class=\"cv-meta\"><span>${esc(INPAINT_TIP)}</span></div>`}" in src


def test_tip_and_identified_target_have_translations():
    en = _read("i18n-en.js")
    es = _read("i18n-es.js")
    assert _q(PT_TIP) in en and _q(EN_TIP) in es
    assert _q("Alvo identificado: {}") in en and _q("Identified target: {}") in es
    assert _q("Objetivo identificado: {}") in es
    assert _q(ES_TIP) in es and ES_TIP != EN_TIP


def test_identified_target_shows_only_without_typed_text_and_escapes_the_value():
    src = _read("canvas.js")
    assert "Alvo identificado: ${esc(r.ident.target)}" in src
    assert "!(n.prompt || '').trim()" in src


def test_new_markup_has_no_inline_style():
    src = _read("canvas.js")
    for line in src.splitlines():
        if "INPAINT_TIP" in line or "Alvo identificado" in line:
            assert "style=" not in line


def test_readme_limitation_section_lists_the_three_models():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^## Limitações conhecidas", readme, re.M)
    section = readme.split("## Limitações conhecidas", 1)[1].split("\n## ", 1)[0]
    for model in ("Nano Banana 2 Lite", "Nano Banana Pro", "GPT-5 Image"):
        assert model in section
    assert "US$" in section
