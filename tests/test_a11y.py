"""Acessibilidade mínima: erro do login anunciado e botões só-ícone com nome acessível."""
import re

from app import config


def test_login_error_is_announced():
    html = (config.STATIC_DIR / "login.html").read_text(encoding="utf-8")
    m = re.search(r'<div[^>]*id="err"[^>]*>', html)
    assert m and 'role="alert"' in m.group(0) and 'aria-live="assertive"' in m.group(0)


def test_icon_only_buttons_have_accessible_name():
    # botão cujo conteúdo é só um ícone ${ic(...)} precisa de aria-label
    pat = re.compile(r"<button(?P<attrs>[^>]*)>\s*\$\{ic\([^)]*\)\}\s*</button>")
    offenders = []
    for f in (config.STATIC_DIR / "js").glob("*.js"):
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            if "aria-label" not in m.group("attrs"):
                offenders.append((f.name, m.group(0)[:100]))
    assert offenders == []


def test_static_icon_only_buttons_have_aria_label():
    html = (config.STATIC_DIR / "studio.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<button([^>]*)>\s*<i data-ic=\"[^\"]*\"></i>\s*</button>", html):
        assert "aria-label" in m.group(1), m.group(0)


def test_upload_has_live_region_and_keyboard_reachable_input():
    html = (config.STATIC_DIR / "studio.html").read_text(encoding="utf-8")
    m = re.search(r'<div[^>]*id="uploadStatus"[^>]*>', html)
    assert m and 'aria-live="polite"' in m.group(0) and 'role="status"' in m.group(0)
    inp = re.search(r'<input[^>]*id="cvFile"[^>]*>', html).group(0)
    assert " hidden" not in inp and "sr-only" in inp  # focável pelo teclado


def test_focus_visible_rules_exist_for_keyboard_users():
    for css in ("studio.css", "admin.css"):
        text = (config.STATIC_DIR / "css" / css).read_text(encoding="utf-8")
        assert ":focus-visible" in text, css
    assert ".sr-only" in (config.STATIC_DIR / "css" / "studio.css").read_text(encoding="utf-8")


def test_mobile_topbar_keeps_accessible_names_and_responsive_rules():
    for css in ("studio.css", "admin.css"):
        text = (config.STATIC_DIR / "css" / css).read_text(encoding="utf-8")
        assert "@media (max-width: 700px)" in text, css
        assert "flex-wrap: wrap" in text, css
    for page in ("studio.html", "admin.html"):
        html = (config.STATIC_DIR / page).read_text(encoding="utf-8")
        for m in re.finditer(r'<button class="tab[^"]*"[^>]*>', html):
            assert 'aria-label="' in m.group(0), (page, m.group(0))
