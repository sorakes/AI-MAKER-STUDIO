"""Provedores de IA por API key.

  openrouter  texto, visão e imagem (chat/completions; imagem via modalities)

As keys ficam cifradas na tabela providers; só são decifradas na hora da chamada.
"""
import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import db
from .config import APP_NAME, PUBLIC_URL
from .security import decrypt, encrypt, mask

PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "kinds": ["text", "vision", "image", "video"], "site": "https://openrouter.ai/keys",
                   "env": "OPENROUTER_API_KEY"},
}
# Sobrescrevíveis por ambiente (proxy corporativo, testes).
OPENROUTER = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")


class ProviderError(RuntimeError):
    pass


# ---------- keys (várias por provedor, cada uma com nome) ----------

def _provider_enabled(pid: str) -> bool:
    r = db.one("SELECT enabled FROM providers WHERE id = ?", (pid,))
    return bool(r["enabled"]) if r else True


def _key_row(pid: str, key_id: int | None = None) -> dict | None:
    """A key pedida (se ativa) ou a padrão do provedor (ou a primeira ativa)."""
    if key_id:
        r = db.one("SELECT * FROM api_keys WHERE id = ? AND provider = ? AND enabled = 1", (key_id, pid))
        if r:
            return r
    return db.one("SELECT * FROM api_keys WHERE provider = ? AND enabled = 1 ORDER BY is_default DESC, id LIMIT 1", (pid,))


def get_key(pid: str, key_id: int | None = None) -> str | None:
    if not _provider_enabled(pid):
        return None
    r = _key_row(pid, key_id)
    if r:
        return decrypt(r["key_enc"])
    return os.getenv(PROVIDERS[pid]["env"]) if pid in PROVIDERS else None  # opcional: key só no ambiente (nunca gravada)


def key_name(pid: str, key_id: int | None = None) -> str:
    r = _key_row(pid, key_id)
    return r["name"] if r else ("ambiente" if pid in PROVIDERS and os.getenv(PROVIDERS[pid]["env"]) else "")


def add_key(pid: str, name: str, key: str) -> int:
    if pid not in PROVIDERS:
        raise ProviderError("provedor desconhecido")
    key = (key or "").strip()
    if not key:
        raise ProviderError("cole a key")
    first = not db.one("SELECT 1 FROM api_keys WHERE provider = ?", (pid,))
    return db.run("INSERT INTO api_keys(provider, name, key_enc, key_hint, is_default, created_at) VALUES(?,?,?,?,?,?)",
                  (pid, (name or "").strip()[:60] or "Sem nome", encrypt(key), mask(key), int(first), db.now()))


def update_key(key_id: int, *, name: str | None = None, key: str | None = None, enabled: bool | None = None,
               is_default: bool | None = None):
    r = db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if not r:
        raise ProviderError("key não encontrada")
    if name is not None:
        db.run("UPDATE api_keys SET name = ? WHERE id = ?", (name.strip()[:60] or r["name"], key_id))
    if key:
        db.run("UPDATE api_keys SET key_enc = ?, key_hint = ? WHERE id = ?", (encrypt(key.strip()), mask(key.strip()), key_id))
    if enabled is not None:
        db.run("UPDATE api_keys SET enabled = ? WHERE id = ?", (int(enabled), key_id))
    if is_default:
        db.run("UPDATE api_keys SET is_default = 0 WHERE provider = ?", (r["provider"],))
        db.run("UPDATE api_keys SET is_default = 1 WHERE id = ?", (key_id,))
    return r["provider"]


def delete_key(key_id: int) -> str:
    r = db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if not r:
        raise ProviderError("key não encontrada")
    db.run("DELETE FROM api_keys WHERE id = ?", (key_id,))
    db.run("UPDATE models SET key_id = NULL WHERE key_id = ?", (key_id,))  # modelos voltam para a padrão
    if r["is_default"]:
        nxt = db.one("SELECT id FROM api_keys WHERE provider = ? ORDER BY id LIMIT 1", (r["provider"],))
        if nxt:
            db.run("UPDATE api_keys SET is_default = 1 WHERE id = ?", (nxt["id"],))
    return r["provider"]


def set_enabled(pid: str, enabled: bool):
    db.run("INSERT INTO providers(id, enabled, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
           "enabled = excluded.enabled, updated_at = excluded.updated_at", (pid, int(enabled), db.now()))


def keys(pid: str) -> list[dict]:
    rows = db.all_("SELECT id, name, key_hint, enabled, is_default, created_at, key_enc FROM api_keys WHERE provider = ? "
                   "ORDER BY is_default DESC, id", (pid,))
    used = {r["key_id"]: r["n"] for r in db.all_("SELECT key_id, COUNT(*) n FROM models WHERE key_id IS NOT NULL GROUP BY key_id")}
    return [{"id": r["id"], "name": r["name"], "hint": r["key_hint"], "enabled": bool(r["enabled"]),
             "is_default": bool(r["is_default"]), "created_at": r["created_at"], "models": used.get(r["id"], 0),
             "readable": decrypt(r["key_enc"]) is not None} for r in rows]


def status() -> list[dict]:
    out = []
    for pid, p in PROVIDERS.items():
        ks = keys(pid)
        env = bool(os.getenv(p["env"]))
        out.append({"id": pid, **p, "keys": ks, "configured": any(k["enabled"] for k in ks) or env,
                    "from_env": env and not ks, "enabled": _provider_enabled(pid),
                    "hint": next((k["hint"] for k in ks if k["is_default"]), "") or ("variável de ambiente" if env else "")})
    return out


def available(pid: str, key_id: int | None = None) -> bool:
    return pid in PROVIDERS and bool(get_key(pid, key_id))


# ---------- HTTP ----------

def _http(method: str, url: str, headers: dict | None = None, body=None, timeout: float = 60, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": f"{APP_NAME}/1.0", **({"Content-Type": "application/json"} if data else {}), **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            return content if raw else json.loads(content or b"{}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(msg)
            msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else (j.get("detail") or j.get("error") or msg)
            if isinstance(msg, list):
                msg = "; ".join(str(m.get("msg", m)) if isinstance(m, dict) else str(m) for m in msg)
        except ValueError:
            pass
        raise ProviderError(f"HTTP {e.code}: {str(msg)[:500]}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"sem conexão: {e.reason}") from e


MAX_DOWNLOAD = 512 * 2**20  # teto por arquivo de saída do provedor
MAX_REDIRECTS = 5


def _resolve_public(host: str, port: int) -> str:
    """Resolve o host UMA vez e devolve um IP público. Qualquer IP interno/loopback/link-local recusa tudo."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ProviderError(f"host de saída não resolve: {host}") from e
    chosen = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ProviderError("URL de saída aponta para endereço interno")
        chosen = chosen or str(ip)
    if not chosen:
        raise ProviderError(f"host de saída não resolve: {host}")
    return chosen


class _PinnedHTTP(http.client.HTTPConnection):
    """Conecta no IP já validado; o cabeçalho Host continua sendo o nome original."""

    def __init__(self, ip: str, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Conecta no IP validado e faz TLS com SNI/validação de certificado do nome original."""

    def __init__(self, ip: str, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _open_pinned(url: str, timeout: float):
    """GET com IP fixado; cada redirecionamento é resolvido e validado de novo (sem segunda consulta de DNS)."""
    for _ in range(MAX_REDIRECTS + 1):
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ProviderError("URL de saída inválida (só http/https)")
        port = p.port or (443 if p.scheme == "https" else 80)
        ip = _resolve_public(p.hostname, port)
        cls = _PinnedHTTPS if p.scheme == "https" else _PinnedHTTP
        conn = cls(ip, p.hostname, port, timeout)
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        try:
            conn.request("GET", path, headers={"User-Agent": f"{APP_NAME}/1.0", "Accept": "*/*"})
            resp = conn.getresponse()
        except OSError as e:
            conn.close()
            raise ProviderError(f"sem conexão: {e}") from e
        if resp.status in (301, 302, 303, 307, 308):
            loc = resp.getheader("Location")
            resp.read()
            conn.close()
            if not loc:
                raise ProviderError(f"HTTP {resp.status} sem Location")
            url = urllib.parse.urljoin(url, loc)
            continue
        if resp.status != 200:
            conn.close()
            raise ProviderError(f"HTTP {resp.status} ao baixar a saída")
        return conn, resp
    raise ProviderError("redirecionamentos demais ao baixar a saída")


def download(url: str, timeout: float = 300) -> bytes:
    if url.startswith("data:"):
        import base64
        return base64.b64decode(url.split(",", 1)[1])
    conn, resp = _open_pinned(url, timeout)
    try:
        declared = resp.getheader("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD:
            raise ProviderError("arquivo de saída grande demais")
        buf = bytearray()
        while chunk := resp.read(64 * 1024):
            buf += chunk
            if len(buf) > MAX_DOWNLOAD:
                raise ProviderError("arquivo de saída grande demais")
        return bytes(buf)
    except OSError as e:
        raise ProviderError(f"falha ao ler a saída: {e}") from e
    finally:
        conn.close()


# ---------- OpenRouter ----------

def _or_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "HTTP-Referer": PUBLIC_URL, "X-Title": APP_NAME}


def openrouter_chat(model_id: str, messages: list[dict], *, as_json: bool = False, extra: dict | None = None,
                    timeout: float = 120, key_id: int | None = None) -> tuple[dict, float]:
    key = get_key("openrouter", key_id)
    if not key:
        raise ProviderError("OpenRouter sem key configurada")
    body = {"model": model_id, "messages": messages, "usage": {"include": True}, **(extra or {})}
    if as_json:
        body["response_format"] = {"type": "json_object"}
    res = _http("POST", f"{OPENROUTER}/chat/completions", _or_headers(key), body, timeout)
    if res.get("error"):
        raise ProviderError(str(res["error"].get("message", res["error"]))[:500])
    return res["choices"][0]["message"], float((res.get("usage") or {}).get("cost") or 0)


def openrouter_audio(model_id: str, messages: list[dict], *, audio: dict | None = None, key_id: int | None = None,
                     timeout: float = 300) -> tuple[bytes, str, float]:
    """Saída de áudio (voz ou música). O OpenRouter exige stream: junta os pedaços base64.
    Devolve (bytes do áudio, transcrição/texto que veio junto, custo em US$)."""
    key = get_key("openrouter", key_id)
    if not key:
        raise ProviderError("OpenRouter sem key configurada")
    body = {"model": model_id, "messages": messages, "modalities": ["text", "audio"], "stream": True,
            "usage": {"include": True}, **({"audio": audio} if audio else {})}
    req = urllib.request.Request(f"{OPENROUTER}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={**_or_headers(key), "Content-Type": "application/json",
                                          "User-Agent": f"{APP_NAME}/1.0"})
    chunks, text, usage = [], [], {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                ev = json.loads(payload)
                if ev.get("error"):
                    raise ProviderError(str(ev["error"].get("message", ev["error"]))[:500])
                usage = ev.get("usage") or usage
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    au = d.get("audio") or {}
                    if au.get("data"):
                        chunks.append(au["data"])
                    if au.get("transcript"):
                        text.append(au["transcript"])
                    if d.get("content"):
                        text.append(d["content"])
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"sem conexão: {e.reason}") from e
    data = b"".join(base64.b64decode(c) for c in chunks)  # cada pedaço é base64 completo: decodifica um a um
    return data, "".join(text), float(usage.get("cost") or 0)


def openrouter_catalog() -> list[dict]:
    """Catálogo público (não gasta crédito): id, modalidades, preço."""
    data = _http("GET", f"{OPENROUTER}/models", timeout=30)["data"]
    return [{"id": m["id"], "name": m.get("name", m["id"]),
             "input": m.get("architecture", {}).get("input_modalities") or [],
             "output": m.get("architecture", {}).get("output_modalities") or [],
             "pricing": m.get("pricing") or {}} for m in data]


def openrouter_video_catalog() -> list[dict]:
    """Catálogo público de vídeo (separado do de texto/imagem)."""
    return _http("GET", f"{OPENROUTER}/videos/models", timeout=30).get("data", [])


def or_video_submit(body: dict, key_id: int | None = None) -> dict:
    key = get_key("openrouter", key_id)
    if not key:
        raise ProviderError("OpenRouter sem key configurada")
    r = _http("POST", f"{OPENROUTER}/videos", _or_headers(key), body, timeout=120)
    if not r.get("id"):
        raise ProviderError(f"OpenRouter não aceitou o vídeo: {str(r)[:300]}")
    return {"provider": "openrouter", "id": r["id"], "polling_url": r.get("polling_url") or f"/api/v1/videos/{r['id']}",
            "key_id": key_id}


def _or_abs(url: str) -> str:
    if url.startswith("http"):
        return url
    base = OPENROUTER.split("/api/")[0]
    return base + url


def or_video_status(handle: dict) -> dict:
    return _http("GET", _or_abs(handle["polling_url"]), _or_headers(get_key("openrouter", handle.get("key_id")) or ""), timeout=30)


def or_video_download(handle: dict, status: dict) -> bytes:
    url = (status.get("unsigned_urls") or [None])[0] or f"{OPENROUTER}/videos/{handle['id']}/content?index=0"
    url = _or_abs(url)
    headers = _or_headers(get_key("openrouter", handle.get("key_id")) or "") if url.startswith(OPENROUTER.split("/api/")[0]) else {}
    return _http("GET", url, headers, timeout=600, raw=True)



# ---------- teste de key ----------

def test(pid: str, key_id: int | None = None) -> dict:
    key = get_key(pid, key_id) if key_id is None else (decrypt(db.one("SELECT key_enc FROM api_keys WHERE id = ?", (key_id,))["key_enc"])
                                                         if db.one("SELECT 1 FROM api_keys WHERE id = ?", (key_id,)) else None)
    if not key:
        return {"ok": False, "message": "Nenhuma key cadastrada"}
    try:
        if pid == "openrouter":
            d = _http("GET", f"{OPENROUTER}/key", _or_headers(key), timeout=20).get("data", {})
            lim = d.get("limit")
            return {"ok": True, "message": f"Key válida · usado US$ {float(d.get('usage') or 0):.2f}"
                                           + (f" de US$ {float(lim):.2f}" if lim else " · sem limite na key")}
        return {"ok": False, "message": "Provedor não suportado"}
    except ProviderError as e:
        return {"ok": False, "message": str(e)}
