import asyncio
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import auth, branding, db, groups, microsoft, storage, usage
from ..config import APP_NAME, COOKIE_SECURE, SESSION_DAYS
from ..security import hash_password, verify_password

router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    try:
        token, u = auth.login(body.email, body.password, ip)
    except HTTPException as e:
        usage.log("login", status="error", detail={"email": body.email.lower()[:120], "ip": ip, "error": e.detail})
        raise
    response.set_cookie(auth.COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE, path="/")
    usage.log("login", user_id=u["id"], detail={"ip": ip})
    return {"ok": True, "role": u["role"]}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/public")
def public_info():
    b = branding.get()
    ms = microsoft.config()
    return {"app_name": b["app_name"], "branding": {k: b[k] for k in ("colors", "style", "logo_url", "tagline", "theme")},
            "microsoft": {"enabled": bool(ms["enabled"]), "ready": microsoft.ready(ms), "label": ms["button_label"] or "Sign in with Microsoft"},
            "default_lang": db.get_setting("default_lang") or "en"}


# ---------- login com Microsoft ----------

def _login_error(msg: str) -> RedirectResponse:
    r = RedirectResponse("/login?error=" + urllib.parse.quote(msg[:300]), status_code=302)
    r.delete_cookie(microsoft.STATE_COOKIE, path="/api/auth/microsoft")
    return r


@router.get("/auth/microsoft/start")
def microsoft_start():
    try:
        url, state = microsoft.start()
    except microsoft.LoginDenied as e:
        return _login_error(str(e))
    r = RedirectResponse(url, status_code=302)
    r.set_cookie(microsoft.STATE_COOKIE, state, max_age=microsoft.STATE_TTL, httponly=True, samesite="lax",
                 secure=COOKIE_SECURE, path="/api/auth/microsoft")
    return r


@router.get("/auth/microsoft/callback")
async def microsoft_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    ip = request.client.host if request.client else "?"
    if error:
        await usage.alog("login", status="error", detail={"provider": "microsoft", "ip": ip, "error": error_description or error})
        return _login_error("Login cancelado" if error == "access_denied" else f"Microsoft: {error_description or error}")
    try:
        u = await asyncio.to_thread(microsoft.finish, code, state, request.cookies.get(microsoft.STATE_COOKIE))
    except microsoft.LoginDenied as e:
        await usage.alog("login", status="error", detail={"provider": "microsoft", "ip": ip, "error": str(e)})
        return _login_error(str(e))
    token = await asyncio.to_thread(auth.create_session, u["id"])
    await usage.alog("login", user_id=u["id"], detail={"ip": ip, "provider": "microsoft", **u.get("_ms", {})})
    r = RedirectResponse("/", status_code=302)
    r.delete_cookie(microsoft.STATE_COOKIE, path="/api/auth/microsoft")
    r.set_cookie(auth.COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
    return r


@router.get("/me")
def me(user: dict = Depends(auth.current_user)):
    b = branding.get()
    return {**user, "app_name": b["app_name"], "branding": {k: b[k] for k in ("colors", "style", "logo_url", "tagline", "theme")},
            "usage": _usage_tokens(user),
            "token_rate": groups.token_rate() if user["is_admin"] else None,
            "default_lang": db.get_setting("default_lang") or "en",
            "storage_bytes": storage.used_bytes(user["id"])}


def _usage_tokens(user: dict) -> dict:
    s = usage.month_stats(user["id"])
    s["tokens"] = groups.to_tokens(s.pop("spend"))
    return s


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/me/password")
def change_password(body: PasswordBody, user: dict = Depends(auth.current_user)):
    row = db.one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
    if row["password_hash"].startswith("!"):
        raise HTTPException(400, "Esta conta entra com Microsoft: a senha é gerenciada lá")
    if not verify_password(body.current, row["password_hash"]):
        raise HTTPException(400, "Senha atual incorreta")
    if len(body.new) < 8:
        raise HTTPException(400, "A nova senha precisa de 8+ caracteres")
    db.run("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(body.new), user["id"]))
    return {"ok": True}
