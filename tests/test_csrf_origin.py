"""Anti-CSRF: escrita com Origin/Referer de outro site é recusada; sem cabeçalho (cliente não-browser) passa."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def user_client(make_user, login):
    make_user("csrf@test.local")
    return login("csrf@test.local", c=TestClient(app))


@pytest.mark.parametrize("origin", ["https://evil.example", "http://evil.example:8080", "null"])
def test_cross_site_write_is_rejected(user_client, origin):
    r = user_client.post("/api/canvas", json={"nodes": [], "edges": []}, headers={"Origin": origin})
    assert r.status_code == 403


def test_cross_site_referer_is_rejected(user_client):
    r = user_client.post("/api/canvas", json={"nodes": []}, headers={"Referer": "https://evil.example/pagina"})
    assert r.status_code == 403


def test_same_host_origin_is_allowed(user_client):
    r = user_client.post("/api/canvas", json={"nodes": []}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_public_url_origin_is_allowed(user_client, monkeypatch):
    # PUBLIC_URL padrão é http://localhost:3000 (atrás de proxy, o host pode ser outro)
    r = user_client.post("/api/canvas", json={"nodes": []}, headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200


def test_no_origin_header_is_allowed(user_client):
    assert user_client.post("/api/canvas", json={"nodes": []}).status_code == 200


def test_reads_ignore_origin(user_client):
    assert user_client.get("/api/canvas", headers={"Origin": "https://evil.example"}).status_code == 200
