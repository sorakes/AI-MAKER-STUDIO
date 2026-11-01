"""Painel admin: usuários, limites, keys, modelos, logs e arquivos."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from .. import auth, branding, db, groups, microsoft, prompts, providers, registry, storage, usage
from ..auth import admin_user, default_limits, public_user
from ..config import APP_NAME
from ..security import hash_password

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])


# ---------- visão geral ----------

@router.get("/overview")
def overview():
    ms = usage.month_start()
    by_kind = db.all_("SELECT kind, COUNT(*) n, COALESCE(SUM(cost),0) cost FROM usage WHERE ts >= ? AND status='ok' "
                      "GROUP BY kind", (ms,))
    errors = db.one("SELECT COUNT(*) n FROM usage WHERE ts >= ? AND status = 'error'", (ms,))["n"]
    top = db.all_("SELECT u.id, u.email, u.name, COALESCE(SUM(g.cost),0) cost, COUNT(g.id) n FROM users u "
                  "LEFT JOIN usage g ON g.user_id = u.id AND g.ts >= ? AND g.status='ok' AND g.kind IN ('image','video','text','vision') "
                  "GROUP BY u.id ORDER BY cost DESC LIMIT 8", (ms,))
    days = db.all_("SELECT CAST((ts - ?) / 86400 AS INT) d, COALESCE(SUM(cost),0) cost, COUNT(*) n FROM usage "
                   "WHERE ts >= ? AND status='ok' AND kind IN ('image','video','text','vision') GROUP BY d ORDER BY d",
                   (ms, ms))
    files = storage.list_files()
    return {"users": db.one("SELECT COUNT(*) n FROM users")["n"],
            "active_users": db.one("SELECT COUNT(DISTINCT user_id) n FROM usage WHERE ts >= ?", (ms,))["n"],
            "by_kind": by_kind, "errors": errors, "top_users": top, "days": days,
            "storage_bytes": sum(f["size"] for f in files), "files": len(files),
            "running_jobs": db.one("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")["n"],
            "providers": providers.status(), "month_start": ms}


# ---------- configurações ----------

class Settings(BaseModel):
    app_name: str | None = None
    default_limits: dict | None = None
    token_rate: float | None = None
    default_lang: str | None = None


@router.get("/settings")
def get_settings():
    return {"app_name": db.get_setting("app_name") or APP_NAME, "default_limits": default_limits(),
            "token_rate": groups.token_rate(), "default_lang": db.get_setting("default_lang") or "en"}


@router.put("/settings")
def put_settings(body: Settings):
    if body.app_name is not None:
        db.set_setting("app_name", body.app_name.strip()[:60] or APP_NAME)
    if body.default_limits is not None:
        db.set_setting("default_limits", _clean_limits(body.default_limits))
    if body.default_lang in ("en", "pt", "es"):
        db.set_setting("default_lang", body.default_lang)
    if body.token_rate is not None:
        if not 0 < body.token_rate <= 1_000_000:
            raise HTTPException(400, "Taxa de tokens inválida")
        db.set_setting("token_rate", body.token_rate)
    return get_settings()


def _clean_limits(lim: dict) -> dict:
    out = {}
    for k in auth.DEFAULT_LIMITS:
        if k not in lim:
            continue
        v = lim[k]
        if k == "models":
            out[k] = [int(x) for x in v] if isinstance(v, list) else None
        else:
            out[k] = None if v in (None, "") else int(float(v))
    return out


def _clean_group_limits(lim: dict) -> dict:
    out = _clean_limits(lim)
    usd = lim.get("budget_usd")
    if usd not in (None, ""):
        out["budget_usd"] = max(0.0, round(float(usd), 2))
        out["budget_split"] = bool(lim.get("budget_split"))
        out.pop("tokens_month", None)  # tokens saem da verba
    return out


# ---------- branding ----------

@router.get("/branding")
def get_branding():
    return {**branding.get(), "themes": branding.THEMES}


@router.put("/branding")
def put_branding(body: dict):
    b = branding.save(body)
    usage.log("admin", detail={"action": "branding"})
    return {**b, "themes": branding.THEMES}


@router.post("/branding/logo")
async def upload_logo(file: UploadFile):
    b = branding.save_logo(file.filename or "logo.png", await file.read(4 * 2**20 + 1))
    return {**b, "themes": branding.THEMES}


@router.delete("/branding/logo")
def delete_logo():
    return {**branding.remove_logo(), "themes": branding.THEMES}


# ---------- prompts dos nós ----------

class PromptBody(BaseModel):
    text: str | None = None  # vazio/None = volta ao padrão


@router.get("/prompts")
def get_prompts():
    return prompts.listing()


@router.put("/prompts/{key}")
def put_prompt(key: str, body: PromptBody):
    try:
        prompts.save(key, body.text)
    except KeyError as e:
        raise HTTPException(404, "prompt desconhecido") from e
    usage.log("admin", detail={"action": "prompt", "key": key, "reset": not (body.text or "").strip()})
    return next(p for p in prompts.listing() if p["key"] == key)


# ---------- usuários ----------

class NewUser(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "user"
    limits: dict = {}
    group_ids: list[int] | None = None


class EditUser(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    active: bool | None = None
    limits: dict | None = None
    password: str | None = None
    group_ids: list[int] | None = None  # grupos manuais (os da Microsoft são automáticos)


@router.get("/users")
def list_users():
    ms = usage.month_start()
    users = [public_user(u) for u in db.all_("SELECT * FROM users ORDER BY created_at")]
    stats = {r["user_id"]: r for r in db.all_(
        "SELECT user_id, SUM(kind='image') images, SUM(kind='video') videos, COALESCE(SUM(cost),0) spend FROM usage "
        "WHERE ts >= ? AND status='ok' GROUP BY user_id", (ms,))}
    for u in users:
        s = stats.get(u["id"], {})
        u["usage"] = {"images": s.get("images") or 0, "videos": s.get("videos") or 0, "spend": round(s.get("spend") or 0, 4),
                      "tokens": groups.to_tokens(s.get("spend") or 0)}
        u["storage_bytes"] = storage.used_bytes(u["id"])
        u["custom_limits"] = json.loads(db.one("SELECT limits FROM users WHERE id = ?", (u["id"],))["limits"] or "{}")
    return users


@router.post("/users")
def create_user(body: NewUser, admin: dict = Depends(admin_user)):
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 8:
        raise HTTPException(400, "E-mail inválido ou senha com menos de 8 caracteres")
    if db.one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "Já existe uma conta com este e-mail")
    uid = db.run("INSERT INTO users(email, name, password_hash, role, limits, created_at) VALUES(?,?,?,?,?,?)",
                 (email, body.name.strip(), hash_password(body.password), "admin" if body.role == "admin" else "user",
                  json.dumps(_clean_limits(body.limits)), time.time()))
    if body.group_ids is not None:
        groups.set_user_manual_groups(uid, body.group_ids)
    usage.log("admin", detail={"action": "create_user", "email": email})
    return public_user(db.one("SELECT * FROM users WHERE id = ?", (uid,)))


@router.patch("/users/{uid}")
def edit_user(uid: int, body: EditUser, admin: dict = Depends(admin_user)):
    u = db.one("SELECT * FROM users WHERE id = ?", (uid,))
    if not u:
        raise HTTPException(404)
    if uid == admin["id"] and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(400, "Você não pode desativar nem rebaixar a própria conta")
    sets: dict = {}
    if body.name is not None:
        sets["name"] = body.name.strip()
    if body.email is not None:
        sets["email"] = body.email.strip().lower()
    if body.role is not None:
        sets["role"] = "admin" if body.role == "admin" else "user"
    if body.active is not None:
        sets["active"] = int(body.active)
    if body.limits is not None:
        sets["limits"] = json.dumps(_clean_limits(body.limits))
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "Senha com menos de 8 caracteres")
        sets["password_hash"] = hash_password(body.password)
    if body.group_ids is not None:
        groups.set_user_manual_groups(uid, body.group_ids)
        if uid == admin["id"] and not _still_admin(uid):
            groups.set_user_manual_groups(uid, [g["id"] for g in admin["groups"] if g["source"] == "manual"])
            raise HTTPException(400, "Isto removeria seu próprio acesso de admin")
    if sets:
        db.run(f"UPDATE users SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), uid))
    if body.active is False or body.password:
        db.run("DELETE FROM sessions WHERE user_id = ?", (uid,))  # derruba sessões abertas
    usage.log("admin", detail={"action": "edit_user", "user": uid, "fields": sorted(k for k in sets if k != "password_hash")
                               + (["password"] if body.password else [])})
    return public_user(db.one("SELECT * FROM users WHERE id = ?", (uid,)))


@router.delete("/users/{uid}")
def delete_user(uid: int, admin: dict = Depends(admin_user)):
    if uid == admin["id"]:
        raise HTTPException(400, "Você não pode excluir a própria conta")
    u = db.one("SELECT email FROM users WHERE id = ?", (uid,))
    if not u:
        raise HTTPException(404)
    freed = storage.delete_user_files(uid)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM canvases WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM jobs WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    usage.log("admin", detail={"action": "delete_user", "email": u["email"], "bytes": freed})
    return {"ok": True, "freed": freed}


# ---------- grupos ----------

class GroupBody(BaseModel):
    name: str
    description: str = ""
    color: str = "#10b981"
    permissions: dict = {}
    limits: dict = {}
    member_ids: list[int] | None = None  # membros manuais


class RuleBody(BaseModel):
    type: str
    value: str


@router.get("/groups")
def list_groups():
    return {"groups": groups.list_all(), "permissions": [{"key": k, "label": l, "help": h} for k, l, h in groups.PERMISSIONS],
            "nodes": [{"key": f"node_{t}", "label": l, "area": a} for t, l, a in groups.NODES],
            "rule_types": groups.RULE_TYPES, "token_rate": groups.token_rate()}


def _save_group(gid: int | None, body: GroupBody) -> dict:
    try:
        gid = groups.save(gid, body.name, body.description, body.color, body.permissions, _clean_group_limits(body.limits))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.member_ids is not None:
        groups.set_manual_members(gid, body.member_ids)
    usage.log("admin", detail={"action": "group", "group": body.name})
    return next(g for g in groups.list_all() if g["id"] == gid)


@router.post("/groups")
def create_group(body: GroupBody):
    return _save_group(None, body)


def _still_admin(uid: int) -> bool:
    return public_user(db.one("SELECT * FROM users WHERE id = ?", (uid,)))["is_admin"]


@router.put("/groups/{gid}")
def edit_group(gid: int, body: GroupBody, admin: dict = Depends(admin_user)):
    old = next((g for g in groups.list_all() if g["id"] == gid), None)
    if not old:
        raise HTTPException(404)
    g = _save_group(gid, body)
    if not _still_admin(admin["id"]):  # tiraria o próprio acesso de admin: desfaz
        groups.save(gid, old["name"], old["description"], old["color"], old["permissions"], old["limits"])
        groups.set_manual_members(gid, [m["user_id"] for m in old["members"] if m["source"] == "manual"])
        raise HTTPException(400, "Isto removeria seu próprio acesso de admin")
    return g


@router.delete("/groups/{gid}")
def delete_group(gid: int, admin: dict = Depends(admin_user)):
    g = db.one("SELECT name, permissions FROM groups WHERE id = ?", (gid,))
    if not g:
        raise HTTPException(404)
    if admin["role"] != "admin" and not any(x["id"] != gid and groups._row(db.one("SELECT * FROM groups WHERE id = ?", (x["id"],)))["permissions"]["admin"]
                                            for x in admin["groups"]):
        raise HTTPException(400, "Este grupo é o que te dá acesso de admin: não dá para excluí-lo")
    groups.delete(gid)
    usage.log("admin", detail={"action": "delete_group", "group": g["name"]})
    return {"ok": True}


@router.post("/groups/{gid}/rules")
def add_rule(gid: int, body: RuleBody):
    if not db.one("SELECT id FROM groups WHERE id = ?", (gid,)):
        raise HTTPException(404)
    try:
        groups.add_rule(gid, body.type, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return next(g for g in groups.list_all() if g["id"] == gid)


@router.delete("/rules/{rid}")
def delete_rule(rid: int):
    groups.delete_rule(rid)
    return {"ok": True}


# ---------- login Microsoft ----------

@router.get("/microsoft")
def get_microsoft():
    return microsoft.public_config()


@router.put("/microsoft")
def put_microsoft(body: dict):
    cfg = microsoft.save(body)
    usage.log("admin", detail={"action": "microsoft_config", "enabled": cfg["enabled"],
                               "secret": "alterado" if body.get("client_secret") else "mantido"})
    return cfg


# ---------- provedores / keys ----------

class NewKey(BaseModel):
    name: str
    key: str


class EditKey(BaseModel):
    name: str | None = None
    key: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ProviderToggle(BaseModel):
    enabled: bool


@router.get("/providers")
def get_providers():
    return providers.status()


@router.put("/providers/{pid}")
def put_provider(pid: str, body: ProviderToggle):
    if pid not in providers.PROVIDERS:
        raise HTTPException(404)
    providers.set_enabled(pid, body.enabled)
    usage.log("admin", detail={"action": "provider_toggle", "provider": pid, "enabled": body.enabled})
    return providers.status()


@router.post("/providers/{pid}/keys")
def add_key(pid: str, body: NewKey):
    try:
        kid = providers.add_key(pid, body.name, body.key)
    except providers.ProviderError as e:
        raise HTTPException(400, str(e)) from e
    usage.log("admin", detail={"action": "key_add", "provider": pid, "name": body.name})
    return {"providers": providers.status(), "test": providers.test(pid, kid)}


@router.patch("/keys/{kid}")
def edit_key(kid: int, body: EditKey):
    try:
        pid = providers.update_key(kid, name=body.name, key=body.key, enabled=body.enabled, is_default=body.is_default)
    except providers.ProviderError as e:
        raise HTTPException(404, str(e)) from e
    usage.log("admin", detail={"action": "key_edit", "provider": pid, "key": kid})
    return providers.status()


@router.delete("/keys/{kid}")
def delete_key(kid: int):
    try:
        pid = providers.delete_key(kid)
    except providers.ProviderError as e:
        raise HTTPException(404, str(e)) from e
    usage.log("admin", detail={"action": "key_delete", "provider": pid, "key": kid})
    return providers.status()


@router.post("/keys/{kid}/test")
def test_key(kid: int):
    r = db.one("SELECT provider FROM api_keys WHERE id = ?", (kid,))
    if not r:
        raise HTTPException(404)
    return providers.test(r["provider"], kid)


@router.post("/providers/{pid}/test")
def test_provider(pid: str):
    if pid not in providers.PROVIDERS:
        raise HTTPException(404)
    return providers.test(pid)


# ---------- modelos ----------

class NewModel(BaseModel):
    provider: str
    model_id: str
    kind: str | None = None
    label: str | None = None
    cost: float | None = None
    cost_unit: str | None = None
    is_default: bool = False
    params: dict = {}


class EditModel(BaseModel):
    key_id: int | None = None   # 0 = usar a key padrão do provedor
    label: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    cost: float | None = None
    cost_unit: str | None = None
    params: dict | None = None
    caps: dict | None = None


@router.get("/models")
def get_models():
    return registry.list_models()


@router.post("/models")
def add_model(body: NewModel):
    try:
        m = registry.add_model(body.provider, body.model_id, body.kind, body.label, body.cost, body.cost_unit,
                               body.is_default, body.params)
    except providers.ProviderError as e:
        raise HTTPException(400, str(e)) from e
    usage.log("admin", detail={"action": "add_model", "model": body.model_id})
    return m


@router.patch("/models/{mid}")
def edit_model(mid: int, body: EditModel):
    m = registry.get(mid)
    if not m:
        raise HTTPException(404)
    sets: dict = {}
    if body.label is not None:
        sets["label"] = body.label.strip() or m["label"]
    if body.enabled is not None:
        sets["enabled"] = int(body.enabled)
    if body.cost is not None:
        sets["cost"] = max(0.0, body.cost)
    if body.cost_unit in ("run", "second"):
        sets["cost_unit"] = body.cost_unit
    if body.params is not None:
        sets["params"] = json.dumps(body.params)
    if body.caps is not None:
        sets["caps"] = json.dumps({**m["caps"], **body.caps})
    if body.key_id is not None:
        if body.key_id and not db.one("SELECT 1 FROM api_keys WHERE id = ? AND provider = ?", (body.key_id, m["provider"])):
            raise HTTPException(400, "Essa key não é deste provedor")
        sets["key_id"] = body.key_id or None
    if sets:
        db.run(f"UPDATE models SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), mid))
    if body.is_default:
        registry.set_default(mid)
    elif body.is_default is False:
        db.run("UPDATE models SET is_default = 0 WHERE id = ?", (mid,))
    return registry.get(mid)


@router.post("/models/{mid}/refresh")
def refresh_model(mid: int):
    try:
        return registry.refresh(mid)
    except providers.ProviderError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/models/{mid}")
def delete_model(mid: int):
    db.run("DELETE FROM models WHERE id = ?", (mid,))
    return {"ok": True}


@router.get("/catalog/{pid}")
def search_catalog(pid: str, q: str = "", kind: str = ""):
    """Sugestões para adicionar modelos (catálogos públicos dos provedores)."""
    try:
        if pid == "openrouter" and kind == "video":
            ql = q.lower()
            out = []
            for m in providers.openrouter_video_catalog():
                if ql and ql not in m["id"].lower() and ql not in (m.get("name") or "").lower():
                    continue
                frames = m.get("supported_frame_images") or []
                price = registry.or_video_price(m.get("pricing_skus") or {})
                durs = m.get("supported_durations") or []
                mx = registry.OR_VIDEO_REFS.get(m["id"], 0)
                out.append({"id": m["id"], "name": m.get("name") or m["id"], "max_refs": mx,
                            "tags": [t for t in ["texto→vídeo", "imagem→vídeo" if "first_frame" in frames else "",
                                                 "frame final" if "last_frame" in frames else "",
                                                 f"{min(durs)}–{max(durs)}s" if durs else ""] if t]
                            + ([f"◐ referências: {registry.OR_VIDEO_REFS_WEAK[m['id']]}"] if mx and m["id"] in registry.OR_VIDEO_REFS_WEAK
                               else [f"★ até {mx} referências"] if mx else ["ignora referências"]),
                            "price": (f"{'~' if price[1] else ''}US$ {price[0]:.3f}/s" if price else "preço não informado")})
            out.sort(key=lambda r: (not r["max_refs"], r["id"] in registry.OR_VIDEO_REFS_WEAK))  # recomendados primeiro
            return out[:40]
        if pid == "openrouter":
            ql = q.lower()
            rows = providers.openrouter_catalog()
            if kind == "image":
                rows = [r for r in rows if "image" in r["output"]]
            elif kind == "vision":
                rows = [r for r in rows if "image" in r["input"]]
            def price(pr):
                parts = []
                if float(pr.get("image_output") or 0):
                    parts.append(f"imagem US$ {float(pr['image_output']) * 1e6:.0f}/M tokens")
                if float(pr.get("prompt") or 0):
                    parts.append(f"entrada US$ {float(pr['prompt']) * 1e6:.2f}/M")
                if float(pr.get("completion") or 0):
                    parts.append(f"saída US$ {float(pr['completion']) * 1e6:.2f}/M")
                return " · ".join(parts) or "grátis"
            return [{"id": r["id"], "name": r["name"], "tags": ["/".join(r["output"])],
                     "price": price(r["pricing"])} for r in rows
                    if not ql or ql in r["id"].lower() or ql in r["name"].lower()][:40]
    except providers.ProviderError as e:
        raise HTTPException(502, str(e)) from e
    raise HTTPException(404)


# ---------- logs ----------

@router.get("/logs")
def logs(user_id: int | None = None, kind: str = "", status: str = "", limit: int = 100, offset: int = 0):
    where, args = [], []
    if user_id:
        where.append("g.user_id = ?"); args.append(user_id)
    if kind:
        where.append("g.kind = ?"); args.append(kind)
    if status:
        where.append("g.status = ?"); args.append(status)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    rows = db.all_(f"SELECT g.*, u.email FROM usage g LEFT JOIN users u ON u.id = g.user_id {w} "
                   f"ORDER BY g.ts DESC LIMIT ? OFFSET ?", (*args, min(limit, 500), offset))
    total = db.one(f"SELECT COUNT(*) n, COALESCE(SUM(cost),0) cost FROM usage g {w}", args)
    return {"rows": rows, "total": total["n"], "cost": total["cost"]}


# ---------- arquivos ----------

@router.get("/files")
def all_files(user_id: int | None = None):
    emails = {u["id"]: u["email"] for u in db.all_("SELECT id, email FROM users")}
    files = storage.list_files(user_id)
    for f in files:
        f["email"] = emails.get(f["user_id"], f"#{f['user_id']} (excluído)")
    return {"files": files, "total": sum(f["size"] for f in files)}


class DeleteFiles(BaseModel):
    urls: list[str]


@router.post("/files/delete")
def delete_files(body: DeleteFiles, admin: dict = Depends(admin_user)):
    freed = sum(storage.delete(u, admin) for u in body.urls)
    usage.log("delete", detail={"files": len(body.urls), "bytes": freed, "by": "admin"})
    return {"ok": True, "freed": freed}
