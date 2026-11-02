"""SSO Microsoft (OIDC + PKCE) com mocks: chave RSA de teste, tokens assinados de verdade, HTTP simulado."""
import base64
import json
import time
import urllib.parse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import db, microsoft
from app.security import decrypt, encrypt

TENANT = "tenant-empresa-id"
CLIENT = "app-cliente-id"
KID = "kid-teste"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _jwk(key) -> dict:
    pub = key.public_key().public_numbers()

    def to(i):
        return _b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))

    return {"kty": "RSA", "kid": KID, "n": to(pub.n), "e": to(pub.e)}


def _token(key, claims: dict, kid: str = KID, alg: str = "RS256") -> str:
    h = _b64(json.dumps({"alg": alg, "kid": kid}).encode())
    p = _b64(json.dumps(claims).encode())
    sig = key.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64(sig)}"


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def cfg(monkeypatch):
    c = {**microsoft.DEFAULTS, "enabled": True, "client_id": CLIENT, "tenant": TENANT,
         "secret_enc": encrypt("segredo-de-teste"), "allow_signup": True}
    monkeypatch.setattr(microsoft, "config", lambda: c)
    microsoft._jwks.update(t=0, keys={})
    return c


@pytest.fixture
def http(monkeypatch, key):
    """Simula a Microsoft: discovery de chaves, troca de code e Graph. Guarda as chamadas."""
    calls = []
    responses = {"discovery": {"keys": [_jwk(key)]}, "token": {}, "memberOf": {"value": []}}

    def fake(url, data=None, headers=None, timeout=20):
        calls.append((url, data))
        for k, v in responses.items():
            if k in url:
                return v
        raise AssertionError(f"chamada inesperada: {url}")

    monkeypatch.setattr(microsoft, "_http", fake)
    return responses, calls


def _claims(**over) -> dict:
    now = time.time()
    base = {"aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "tid": TENANT,
            "oid": "oid-ana", "email": "ana@empresa.test", "name": "Ana",
            "exp": now + 300, "nbf": now - 10, "sub": "sub-ana"}
    base.update(over)
    return base


def _start(cfg):
    url, cookie = microsoft.start()
    st = json.loads(decrypt(cookie))
    return url, cookie, st


def test_start_builds_pkce_authorization_url(cfg):
    url, _cookie, st = _start(cfg)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q["client_id"] == [CLIENT] and q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == [st["state"]] and q["nonce"] == [st["nonce"]]
    assert url.startswith(f"{microsoft.AUTHORITY}/{TENANT}/oauth2/v2.0/authorize")
    assert "verifier" not in url  # o verificador fica só no cookie cifrado


def test_full_login_creates_account_from_valid_token(cfg, http, key):
    responses, calls = http
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st["nonce"])), "access_token": "at-1"}
    u = microsoft.finish("code-123", st["state"], cookie)
    assert u["email"] == "ana@empresa.test" and u["auth_provider"] == "microsoft"
    token_call = [c for c in calls if "/token" in c[0]][0][1]
    assert token_call["code"] == "code-123" and token_call["code_verifier"] == st["verifier"]
    assert db.one("SELECT ext_id FROM users WHERE id = ?", (u["id"],))["ext_id"] == f"{TENANT}:oid-ana"


def test_second_login_reuses_the_same_account(cfg, http, key):
    responses, _ = http
    _, c1, st1 = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st1["nonce"]))}
    first = microsoft.finish("c1", st1["state"], c1)
    _, c2, st2 = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st2["nonce"]))}
    second = microsoft.finish("c2", st2["state"], c2)
    assert first["id"] == second["id"]


def test_state_mismatch_is_rejected(cfg, http, key):
    responses, _ = http
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st["nonce"]))}
    with pytest.raises(microsoft.LoginDenied, match="expirou|outra janela"):
        microsoft.finish("code", "estado-forjado", cookie)


def test_forged_signature_is_rejected(cfg, http):
    responses, _ = http
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(other, _claims(nonce=st["nonce"]))}  # assinado por outra chave
    with pytest.raises(microsoft.LoginDenied, match="Assinatura"):
        microsoft.finish("code", st["state"], cookie)


def test_alg_none_is_rejected(cfg, http):
    responses, _ = http
    _, cookie, st = _start(cfg)
    h = _b64(json.dumps({"alg": "none", "kid": KID}).encode())
    p = _b64(json.dumps(_claims(nonce=st["nonce"])).encode())
    responses["token"] = {"id_token": f"{h}.{p}."}
    with pytest.raises(microsoft.LoginDenied, match="Algoritmo"):
        microsoft.finish("code", st["state"], cookie)


@pytest.mark.parametrize("over,msg", [
    ({"aud": "outro-app"}, "outro aplicativo"),
    ({"iss": "https://evil.example/v2.0"}, "Emissor"),
    ({"exp": time.time() - 3600}, "expirado"),
    ({"tid": "outro-tenant", "iss": "https://login.microsoftonline.com/outro-tenant/v2.0"}, "tenant"),
    ({"nonce": "nonce-errado"}, "nonce"),
])
def test_invalid_claims_are_rejected(cfg, http, key, over, msg):
    responses, _ = http
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(**{"nonce": st["nonce"], **over}))}
    with pytest.raises(microsoft.LoginDenied, match=msg):
        microsoft.finish("code", st["state"], cookie)


def test_domain_outside_allowed_list_is_rejected(cfg, http, key):
    responses, _ = http
    cfg["allowed_domains"] = ["empresa.test"]
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st["nonce"], email="x@fora.test", oid="oid-x"))}
    with pytest.raises(microsoft.LoginDenied, match="não tem acesso"):
        microsoft.finish("code", st["state"], cookie)


def test_signup_disabled_blocks_unknown_account(cfg, http, key):
    responses, _ = http
    cfg["allow_signup"] = False
    _, cookie, st = _start(cfg)
    responses["token"] = {"id_token": _token(key, _claims(nonce=st["nonce"], email="nova@empresa.test", oid="oid-nova"))}
    with pytest.raises(microsoft.LoginDenied, match="liberada"):
        microsoft.finish("code", st["state"], cookie)


def test_graph_groups_follow_pagination(monkeypatch):
    pages = iter([{"value": [{"id": "g1"}], "@odata.nextLink": "https://graph.example/page2"},
                  {"value": [{"id": "g2"}, {"id": "g3"}]}])
    seen = []

    def paged(url, data=None, headers=None, timeout=20):
        seen.append(url)
        return next(pages)

    monkeypatch.setattr(microsoft, "_http", paged)
    assert microsoft._graph_groups("at") == ["g1", "g2", "g3"]
    assert seen[1] == "https://graph.example/page2"


def test_provider_error_becomes_login_denied(cfg, monkeypatch):
    def boom(*a, **k):
        raise microsoft.LoginDenied("Microsoft recusou: invalid_grant")

    monkeypatch.setattr(microsoft, "_http", boom)
    _, cookie, st = _start(cfg)
    with pytest.raises(microsoft.LoginDenied, match="invalid_grant"):
        microsoft.finish("code", st["state"], cookie)


def test_start_refuses_when_not_configured(monkeypatch):
    monkeypatch.setattr(microsoft, "config", lambda: {**microsoft.DEFAULTS, "enabled": False})
    with pytest.raises(microsoft.LoginDenied, match="configurado"):
        microsoft.start()
