"""Login com Microsoft (Entra ID / Azure AD) por OpenID Connect: code flow + PKCE + nonce.

O id_token é validado de verdade: assinatura RS256 pelas chaves públicas da Microsoft (JWKS),
emissor, audiência (client_id), validade e nonce. O client secret fica cifrado no banco como as API keys.

Atribuição automática: a cada login os grupos "microsoft" da conta são recalculados pelas regras
(domínio, grupo do Entra, app role, tenant); grupos dados à mão no painel não são mexidos.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import db, groups, usage
from .config import PUBLIC_URL
from .security import decrypt, encrypt, hash_password, mask

AUTHORITY = os.getenv("MS_AUTHORITY", "https://login.microsoftonline.com").rstrip("/")
GRAPH = os.getenv("MS_GRAPH_URL", "https://graph.microsoft.com/v1.0").rstrip("/")
MULTI = {"common", "organizations", "consumers"}
STATE_COOKIE = "ms_oauth"
STATE_TTL = 600

DEFAULTS = {"enabled": False, "tenant": "organizations", "client_id": "", "secret_enc": None, "secret_hint": "",
            "public_url": "", "allow_signup": True, "allowed_domains": [], "default_group_id": None,
            "require_match": False, "graph_groups": False, "button_label": "Entrar com Microsoft"}


class LoginDenied(Exception):
    pass


# ---------- configuração ----------

def config() -> dict:
    return {**DEFAULTS, **(db.get_setting("microsoft") or {})}


def public_config() -> dict:
    c = config()
    out = {k: v for k, v in c.items() if k != "secret_enc"}
    out["has_secret"] = bool(c.get("secret_enc")) and decrypt(c["secret_enc"]) is not None
    out["redirect_uri"] = redirect_uri(c)
    out["ready"] = ready(c)
    out["missing"] = missing(c)
    return out


def ready(c: dict | None = None) -> bool:
    c = c or config()
    return bool(c["enabled"] and c["client_id"] and c.get("secret_enc") and c["tenant"])


def missing(c: dict | None = None) -> list[str]:
    c = c or config()
    out = []
    if not c["client_id"]:
        out.append("Client ID")
    if not c.get("secret_enc") or decrypt(c["secret_enc"]) is None:
        out.append("Client secret")
    if not c["tenant"]:
        out.append("Tenant")
    return out


def save(body: dict) -> dict:
    c = config()
    for k in ("enabled", "allow_signup", "require_match", "graph_groups"):
        if k in body:
            c[k] = bool(body[k])
    for k in ("tenant", "client_id", "public_url", "button_label"):
        if k in body and body[k] is not None:
            c[k] = str(body[k]).strip()[:300]
    if "allowed_domains" in body:
        raw = body["allowed_domains"]
        items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").replace("\n", ",").split(",")
        c["allowed_domains"] = sorted({d.strip().lower().lstrip("@") for d in items if d.strip()})
    if "default_group_id" in body:
        gid = body["default_group_id"]
        c["default_group_id"] = int(gid) if gid and db.one("SELECT id FROM groups WHERE id = ?", (int(gid),)) else None
    if body.get("client_secret"):  # só substitui quando vem um novo; nunca volta para o navegador
        s = str(body["client_secret"]).strip()
        c["secret_enc"], c["secret_hint"] = encrypt(s), mask(s)
    if body.get("clear_secret"):
        c["secret_enc"], c["secret_hint"] = None, ""
    c["tenant"] = c["tenant"] or "organizations"
    db.set_setting("microsoft", c)
    return public_config()


def redirect_uri(c: dict | None = None) -> str:
    c = c or config()
    base = (c.get("public_url") or PUBLIC_URL).rstrip("/")
    return f"{base}/api/auth/microsoft/callback"


# ---------- fluxo ----------

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def start() -> tuple[str, str]:
    """URL de autorização + valor do cookie de estado (cifrado, 10 min)."""
    c = config()
    if not ready(c):
        raise LoginDenied("O login com Microsoft ainda não foi configurado pelo administrador (falta " + ", ".join(missing(c)) + ").")
    st = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
          "verifier": secrets.token_urlsafe(48), "t": time.time()}
    scope = "openid profile email" + (" User.Read GroupMember.Read.All" if c["graph_groups"] else "")
    q = {"client_id": c["client_id"], "response_type": "code", "redirect_uri": redirect_uri(c), "response_mode": "query",
         "scope": scope, "state": st["state"], "nonce": st["nonce"], "prompt": "select_account",
         "code_challenge": _b64url(hashlib.sha256(st["verifier"].encode()).digest()), "code_challenge_method": "S256"}
    return f"{AUTHORITY}/{urllib.parse.quote(c['tenant'])}/oauth2/v2.0/authorize?{urllib.parse.urlencode(q)}", encrypt(json.dumps(st))


def _http(url: str, data: dict | None = None, headers: dict | None = None, timeout: int = 20) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            inner = err.get("error")
            msg = err.get("error_description") or (inner.get("message") if isinstance(inner, dict) else inner) or str(e)
        except ValueError:
            msg = str(e)
        raise LoginDenied(f"Microsoft recusou: {str(msg)[:300]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LoginDenied(f"Não foi possível falar com a Microsoft: {e}") from e


_jwks: dict = {"t": 0, "keys": {}}


def _signing_key(kid: str, tenant: str):
    if kid not in _jwks["keys"] or time.time() - _jwks["t"] > 3600:
        data = _http(f"{AUTHORITY}/{urllib.parse.quote(tenant)}/discovery/v2.0/keys")
        _jwks.update(t=time.time(), keys={k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"})
    k = _jwks["keys"].get(kid)
    if not k:
        raise LoginDenied("Chave de assinatura do token desconhecida")
    n, e = (int.from_bytes(_b64url_dec(k[x]), "big") for x in ("n", "e"))
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify_id_token(token: str, c: dict, nonce: str) -> dict:
    try:
        h64, p64, s64 = token.split(".")
        header, claims = json.loads(_b64url_dec(h64)), json.loads(_b64url_dec(p64))
    except ValueError as e:
        raise LoginDenied("Token da Microsoft inválido") from e
    if header.get("alg") != "RS256":
        raise LoginDenied("Algoritmo do token não aceito")
    try:
        _signing_key(header.get("kid", ""), c["tenant"]).verify(
            _b64url_dec(s64), f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise LoginDenied("Assinatura do token inválida") from e
    now = time.time()
    tid = str(claims.get("tid", ""))
    if claims.get("aud") != c["client_id"]:
        raise LoginDenied("Token emitido para outro aplicativo")
    if claims.get("iss") not in {f"https://login.microsoftonline.com/{tid}/v2.0", f"{AUTHORITY}/{tid}/v2.0"}:
        raise LoginDenied("Emissor do token inválido")
    if c["tenant"].lower() not in MULTI and tid.lower() != c["tenant"].lower():
        raise LoginDenied("Conta de outro diretório (tenant) não é aceita")
    if float(claims.get("exp", 0)) < now - 60 or float(claims.get("nbf", 0)) > now + 60:
        raise LoginDenied("Token expirado: tente de novo")
    if not nonce or claims.get("nonce") != nonce:
        raise LoginDenied("Sessão de login inválida (nonce)")
    return claims


def _graph_groups(access_token: str) -> list[str]:
    out, url = [], f"{GRAPH}/me/memberOf?$select=id&$top=999"
    while url and len(out) < 5000:
        d = _http(url, headers={"Authorization": f"Bearer {access_token}"})
        out += [x["id"] for x in d.get("value", []) if x.get("id")]
        url = d.get("@odata.nextLink")
    return out


def finish(code: str, state: str, cookie: str | None) -> dict:
    """Troca o code pelo token, valida e devolve a conta (criando/vinculando e sincronizando grupos)."""
    c = config()
    if not ready(c):
        raise LoginDenied("Login com Microsoft não está configurado")
    try:
        st = json.loads(decrypt(cookie or "") or "{}")
    except ValueError:
        st = {}
    if not st or not secrets.compare_digest(str(st.get("state", "")), state or "") or time.time() - st.get("t", 0) > STATE_TTL:
        raise LoginDenied("O login expirou ou veio de outra janela: tente de novo")
    tok = _http(f"{AUTHORITY}/{urllib.parse.quote(c['tenant'])}/oauth2/v2.0/token", {
        "client_id": c["client_id"], "client_secret": decrypt(c["secret_enc"]) or "", "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri(c), "code_verifier": st["verifier"]})
    claims = verify_id_token(tok.get("id_token", ""), c, st["nonce"])
    return login_from_claims(claims, c, tok.get("access_token"))


def login_from_claims(claims: dict, c: dict, access_token: str | None = None) -> dict:
    oid, tid = str(claims.get("oid") or claims.get("sub") or ""), str(claims.get("tid") or "")
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not oid or "@" not in email:
        raise LoginDenied("A conta Microsoft não informou e-mail")
    domain = email.rsplit("@", 1)[1]
    if c["allowed_domains"] and domain not in c["allowed_domains"]:
        raise LoginDenied(f"O domínio @{domain} não tem acesso a este estúdio")
    entra_groups = [str(g) for g in claims.get("groups") or []]
    if c["graph_groups"] and access_token:
        try:
            entra_groups = sorted(set(entra_groups) | set(_graph_groups(access_token)))
        except LoginDenied as e:
            usage.log("login", status="error", detail={"email": email, "warn": f"grupos via Graph: {e}"})
    roles = [str(r) for r in claims.get("roles") or []]
    ext = f"{tid}:{oid}"

    u = db.one("SELECT * FROM users WHERE auth_provider = 'microsoft' AND ext_id = ?", (ext,))
    if not u:
        local = db.one("SELECT * FROM users WHERE email = ?", (email,))
        if local:
            # Vincular pelo e-mail só com um tenant fixo: em modo multi-tenant outro diretório poderia usar o mesmo e-mail.
            if c["tenant"].lower() in MULTI:
                raise LoginDenied("Já existe uma conta com este e-mail. Para vincular, configure o Tenant ID da empresa no painel.")
            db.run("UPDATE users SET auth_provider = 'microsoft', ext_id = ? WHERE id = ?", (ext, local["id"]))
            u = db.one("SELECT * FROM users WHERE id = ?", (local["id"],))
        elif c["allow_signup"]:
            uid = db.run("INSERT INTO users(email, name, password_hash, role, limits, created_at, auth_provider, ext_id) "
                         "VALUES(?,?,?,?,?,?,?,?)", (email, str(claims.get("name") or "")[:120],
                                                     "!" + hash_password(secrets.token_urlsafe(32)),  # sem senha local
                                                     "user", "{}", time.time(), "microsoft", ext))
            u = db.one("SELECT * FROM users WHERE id = ?", (uid,))
            usage.log("admin", user_id=uid, detail={"action": "microsoft_signup", "email": email})
        else:
            raise LoginDenied("Sua conta ainda não foi liberada. Fale com o administrador.")
    if not u["active"]:
        raise LoginDenied("Conta desativada. Fale com o administrador.")

    matched = groups.match_rules(email, tid, entra_groups, roles)
    if not matched and c["default_group_id"]:
        matched = {c["default_group_id"]}
    manual = db.one("SELECT COUNT(*) n FROM user_groups WHERE user_id = ? AND source = 'manual'", (u["id"],))["n"]
    if c["require_match"] and not matched and not manual and u["role"] != "admin":
        raise LoginDenied("Sua conta Microsoft não está em nenhum grupo liberado para este estúdio.")
    groups.sync_microsoft(u["id"], matched)
    if not u["name"] and claims.get("name"):
        db.run("UPDATE users SET name = ? WHERE id = ?", (str(claims["name"])[:120], u["id"]))
    u["_ms"] = {"groups": len(entra_groups), "roles": roles, "matched": sorted(matched)}
    return u
