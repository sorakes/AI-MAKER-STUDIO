"""CSP das páginas: sem script inline nem handler inline, e cabeçalho presente."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.main import PAGE_CSP, app

PAGES = ["login.html", "studio.html", "admin.html"]


def test_pages_have_no_inline_scripts():
    for name in PAGES:
        html = (config.STATIC_DIR / name).read_text(encoding="utf-8")
        inline = re.findall(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", html, re.S)
        assert [s.strip() for s in inline if s.strip()] == [], name
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html), name


def test_js_has_no_inline_handlers_in_templates():
    for f in (config.STATIC_DIR / "js").glob("*.js"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"<[a-z][^>]*\son(click|mouse\w+|change|input|submit|error|load)\s*=", text), f.name


def test_html_pages_send_csp_header():
    c = TestClient(app)
    r = c.get("/login")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == PAGE_CSP
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "unsafe-eval" not in PAGE_CSP and "'unsafe-inline'" not in PAGE_CSP.split("style-src")[0]


def test_media_sources_are_local_only():
    csp = PAGE_CSP
    assert "https:" not in csp.split("img-src")[1].split(";")[0]
    assert "https:" not in csp.split("media-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in csp
