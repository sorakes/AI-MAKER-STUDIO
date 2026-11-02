"""Link público temporário para o provedor buscar um arquivo local.

Alguns modelos (ex.: edição de vídeo) só aceitam uma URL HTTPS pública como entrada. O docker-compose sobe um
`cloudflared` (túnel rápido da Cloudflare, sem conta) apontando para o app. Daqui saem links `/pub/<token>/<nome>`:

  - o token é aleatório (192 bits), vale para UM arquivo e expira sozinho (TTL) ou assim que o resultado chega (revoke);
  - pelo túnel só `/pub/` responde (ver `app.main.tunnel_gate`): login, estúdio e API nunca ficam expostos;
  - sem túnel (rodando fora do compose) `available()` é falso e o app usa o caminho por quadros.
"""
import json
import os
import secrets
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

METRICS_URL = os.environ.get("TUNNEL_METRICS_URL", "http://tunnel:2000")  # servidor de métricas do cloudflared
FIXED_HOST = os.environ.get("TUNNEL_HOST", "").strip().lower()  # túnel nomeado: domínio fixo, sem consulta
TTL_S = int(os.environ.get("TUNNEL_LINK_TTL_S", str(45 * 60)))  # teto de vida do link
MAX_LINKS = 500  # contra enchente de links abertos

_links: dict[str, dict] = {}
_lock = threading.Lock()
_host = {"t": 0.0, "v": ""}


class TunnelError(RuntimeError):
    pass


def public_host() -> str:
    """Host público atual do túnel ('' se não houver). Cache curto: o host muda quando o túnel reinicia."""
    if FIXED_HOST:
        return FIXED_HOST
    now = time.time()
    if now - _host["t"] < 20:
        return _host["v"]
    host = ""
    try:
        with urllib.request.urlopen(METRICS_URL.rstrip("/") + "/quicktunnel", timeout=2) as r:  # noqa: S310
            host = (json.load(r).get("hostname") or "").strip().lower()
    except Exception:  # noqa: BLE001 - sem túnel: indisponível, não é erro
        host = ""
    _host.update(t=now, v=host)
    return host


def available() -> bool:
    return bool(public_host())


def is_tunnel_request(host_header: str, has_cf_header: bool) -> bool:
    """A requisição veio pelo túnel (e não do próprio PC/rede local)?"""
    h = (host_header or "").split(":")[0].lower()
    return has_cf_header or h.endswith(".trycloudflare.com") or (bool(FIXED_HOST) and h == FIXED_HOST)


def _purge(now: float) -> None:
    for t in [t for t, v in _links.items() if v["exp"] <= now]:
        _links.pop(t, None)


def create(path: Path, ttl: float | None = None) -> tuple[str, str]:
    """Abre um link para `path`. Devolve (token, url pública https)."""
    host = public_host()
    if not host:
        raise TunnelError("túnel público indisponível")
    if not Path(path).is_file():
        raise TunnelError("arquivo não encontrado")
    now = time.time()
    token = secrets.token_urlsafe(24)
    with _lock:
        _purge(now)
        if len(_links) >= MAX_LINKS:
            raise TunnelError("links públicos demais abertos; tente de novo em instantes")
        _links[token] = {"path": Path(path), "name": Path(path).name, "exp": now + min(ttl or TTL_S, TTL_S)}
    return token, f"https://{host}/pub/{token}/{Path(path).name}"


def revoke(token: str) -> None:
    with _lock:
        _links.pop(token, None)


def lookup(token: str, name: str) -> Path | None:
    """Arquivo do link se ele existir, não tiver expirado e o nome bater; senão None."""
    now = time.time()
    with _lock:
        _purge(now)
        v = _links.get(token)
    if not v or v["name"] != name or not v["path"].is_file():
        return None
    return v["path"]


def open_links() -> int:
    with _lock:
        _purge(time.time())
        return len(_links)


@contextmanager
def shared(path: Path, ttl: float | None = None):
    """`with tunnel.shared(arquivo) as url:` — o link some ao sair do bloco, com ou sem erro."""
    token, url = create(path, ttl)
    try:
        yield url
    finally:
        revoke(token)
