"""Logo SVG: só SVG estático; servido com CSP sandbox."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import branding
from app.main import app

OK_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#10b981"/></svg>'
OK_SVG_INTERNAL_USE = b'<svg xmlns="http://www.w3.org/2000/svg"><defs><g id="a"/></defs><use href="#a"/></svg>'

BAD = [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick = "x()"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body/></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a xlink:href="javascript:alert(1)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://evil.example/p)"/></svg>',
    b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect',  # XML malformado
    b'<html><body>nao e svg</body></html>',
]


def test_accepts_static_svg():
    branding.check_svg(OK_SVG)
    branding.check_svg(OK_SVG_INTERNAL_USE)


@pytest.mark.parametrize("data", BAD)
def test_rejects_active_or_external_svg(data):
    with pytest.raises(HTTPException) as e:
        branding.check_svg(data)
    assert e.value.status_code == 400


def test_upload_and_serve_logo_with_csp_sandbox(login):
    c = login("admin@test.local", "admin-test-pw", c=TestClient(app))
    bad = c.post("/api/admin/branding/logo", files={"file": ("x.svg", BAD[0], "image/svg+xml")})
    assert bad.status_code == 400
    ok = c.post("/api/admin/branding/logo", files={"file": ("logo.svg", OK_SVG, "image/svg+xml")})
    assert ok.status_code == 200, ok.text
    name = branding.get()["logo"]
    r = TestClient(app).get(f"/branding/{name}")
    assert r.status_code == 200
    assert "sandbox" in r.headers["content-security-policy"]
