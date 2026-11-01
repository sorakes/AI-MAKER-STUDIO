"""providers.download: só http(s), IP público fixado (sem DNS rebinding), redirects revalidados e teto de bytes."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import providers
from app.providers import ProviderError

_real_resolve = providers._resolve_public


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


def test_resolve_refuses_any_internal_address(monkeypatch):
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    with pytest.raises(ProviderError, match="interno"):
        _real_resolve("misto.test", 80)


class _Handler(BaseHTTPRequestHandler):
    seen_hosts = []

    def do_GET(self):
        _Handler.seen_hosts.append(self.headers.get("Host"))
        if self.path == "/redir-loop-local":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/big")  # destino interno: tem de ser recusado
            self.end_headers()
            return
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
def server(monkeypatch):
    """Servidor local; o nome 'exemplo.test' é fixado no loopback (testa o caminho com IP pinado)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    _Handler.seen_hosts.clear()

    def resolve(host, port_):
        return "127.0.0.1" if host == "exemplo.test" else _real_resolve(host, port_)

    monkeypatch.setattr(providers, "_resolve_public", resolve)
    yield f"http://exemplo.test:{port}"
    srv.shutdown()


def test_download_connects_to_pinned_ip_and_keeps_host(server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    assert providers.download(f"{server}/small") == b"x" * 10
    assert _Handler.seen_hosts[-1] == f"exemplo.test:{server.rsplit(':', 1)[1]}"


def test_name_is_resolved_once_per_hop(monkeypatch):
    """Uma consulta de DNS por salto (a conexão usa o IP devolvido, sem consultar o nome de novo)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    lookups = []

    def resolve(host, port_):
        if host == "exemplo.test":
            lookups.append(host)
            return "127.0.0.1"
        return _real_resolve(host, port_)

    monkeypatch.setattr(providers, "_resolve_public", resolve)
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    try:
        providers.download(f"http://exemplo.test:{port}/small")
        assert len(lookups) == 1
        providers.download(f"http://exemplo.test:{port}/redir")  # dois saltos: duas consultas, uma por salto
        assert len(lookups) == 3
    finally:
        srv.shutdown()


def test_download_enforces_byte_cap(server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 1000)
    with pytest.raises(ProviderError, match="grande demais"):
        providers.download(f"{server}/big")


def test_download_follows_redirect_within_cap(server, monkeypatch):
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    assert len(providers.download(f"{server}/redir")) == 4096


def test_redirect_to_internal_address_is_blocked(server):
    with pytest.raises(ProviderError, match="interno"):
        providers.download(f"{server}/redir-loop-local")


def _self_signed(tmp_path, cn="exemplo.test"):
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def test_https_pinned_ip_uses_sni_and_validates_certificate(tmp_path, monkeypatch):
    import ssl

    cert, key = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    sni_seen = []
    server_ctx.sni_callback = lambda sock, name, ctx: sni_seen.append(name)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    real_ctx = ssl.create_default_context

    def client_ctx():
        return real_ctx(cafile=str(cert))  # confia só no certificado de teste

    monkeypatch.setattr(providers.ssl, "create_default_context", client_ctx)
    monkeypatch.setattr(providers, "_resolve_public",
                        lambda h, p: "127.0.0.1" if h == "exemplo.test" else _real_resolve(h, p))
    monkeypatch.setattr(providers, "MAX_DOWNLOAD", 10_000)
    try:
        assert providers.download(f"https://exemplo.test:{port}/small") == b"x" * 10
        assert "exemplo.test" in sni_seen  # SNI com o nome original, não com o IP
        # nome que não está no certificado: a validação tem de recusar
        monkeypatch.setattr(providers, "_resolve_public",
                            lambda h, p: "127.0.0.1" if h == "outro.test" else _real_resolve(h, p))
        with pytest.raises(ProviderError):
            providers.download(f"https://outro.test:{port}/small")
    finally:
        srv.shutdown()
