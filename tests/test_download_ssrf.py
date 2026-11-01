"""providers.download: só http(s), só IP público, redirects revalidados e teto de bytes."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import providers
from app.providers import ProviderError


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://exemplo.com/a.mp4",
    "http://127.0.0.1/a.mp4",
    "http://localhost/a.mp4",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/a.mp4",
    "http://192.168.1.10/a.mp4",
    "http://[::1]/a.mp4",
    "http://[::ffff:127.0.0.1]/a.mp4",
])
def test_blocks_non_public_targets(url):
    with pytest.raises(ProviderError):
        providers.download(url)


def test_redirect_to_private_address_is_blocked():
    handler = providers._CheckedRedirect()
    with pytest.raises(ProviderError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:8080/x")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/big")
            self.end_headers()
            return
        size = 4096 if self.path == "/big" else 10
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(b"x" * size)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server(monkeypatch):
    # o servidor de teste é loopback: libera só a checagem de IP, mantendo o resto do fluxo
    monkeypatch.setattr(providers, "_check_public_url", lambda url: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_download_returns_bytes(local_server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    assert providers.download(f"{local_server}/small") == b"x" * 10


def test_download_enforces_byte_cap(local_server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 1000)
    with pytest.raises(ProviderError, match="grande demais"):
        providers.download(f"{local_server}/big")


def test_download_follows_redirect_within_cap(local_server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    assert len(providers.download(f"{local_server}/redir")) == 4096
