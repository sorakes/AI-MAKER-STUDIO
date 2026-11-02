"""Nada vaza do node: regras de layout presentes e os dois scripts de Chrome (vazamento e foco) no repositório."""
import re

from app import config

ROOT = config.BASE_DIR
CSS = (config.STATIC_DIR / "css" / "studio.css").read_text(encoding="utf-8")
CANVAS = (config.STATIC_DIR / "js" / "canvas.js").read_text(encoding="utf-8")


def test_layout_rules_keep_node_content_inside_its_width():
    for rule in (".cv-node .cv-content, .cv-node .cv-content > *", ".cv-node .cv-plan > span", ".cv-node .cv-plan > b",
                 ".cv-node .cv-step, .cv-node .cv-chip", ".cv-node .cv-warn, .cv-node .cv-error span",
                 ".cv-htext > small, .cv-htext .title", ".cv-node select, .cv-node input"):
        assert rule in CSS, rule
    assert re.search(r"\.cv-node \.cv-plan > b \{ flex-shrink: 0; \}", CSS)  # a estimativa não encolhe nem empurra


def test_long_model_names_carry_their_full_name_in_title():
    assert 'class="cv-step ${s.kind}" title="${esc(s.m.label)}"' in CANVAS
    assert 'class="cv-step image" title="${esc(m.label)}"' in CANVAS


def test_no_inline_style_attribute_was_added_to_the_layout_code():
    assert 'style="' not in CANVAS


def test_checking_scripts_are_in_the_repository():
    for name in ("check_node_overflow.mjs", "check_text_focus.mjs"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "9333" in src and "process.exit" in src
    assert "controlDetected" in (ROOT / "scripts" / "check_node_overflow.mjs").read_text(encoding="utf-8")
