"""Grupos: permissões, limites e atribuição automática (regras do login Microsoft).

Permissões efetivas de uma conta:
  - sem nenhum grupo  -> BASE (tudo do estúdio liberado, sem painel admin): igual ao comportamento antigo;
  - com grupos        -> soma das permissões dos grupos (basta um grupo liberar).
Limites efetivos (por campo): limite próprio da conta > o mais generoso entre os grupos que definem o campo > padrão.
"""
import json
import time

from . import db

PERMISSIONS = [
    # (chave, rótulo, descrição)
    ("ws_video", "Video Studio", "Aba de vídeo: nós de imagem, vídeo, transição e montagem"),
    ("ws_character", "Personagens", "Aba de personagens (posições, spritesheet)"),
    ("ws_product", "Produtos", "Aba de produtos"),
    ("gen_image", "Gerar imagens", "Pode rodar modelos de imagem"),
    ("gen_video", "Gerar vídeos", "Pode rodar modelos de vídeo"),
    ("ai_assist", "Assistentes de IA", "Prompt automático, roteiro, leitura da câmera, sugestões"),
    ("upload", "Enviar arquivos", "Upload de imagens, vídeos e áudio"),
    ("delete_files", "Apagar arquivos", "Apagar arquivos próprios"),
    ("admin", "Painel admin", "Acesso total ao painel de administração"),
]
# Permissões por node (paleta). Grupo que nunca salvou um node = liberado (não quebra grupos antigos).
NODES = [
    # (tipo, rótulo, área)
    ("brief", "Roteiro", "video"), ("cast", "Elenco / produto", "video"), ("video", "Gerar vídeo", "video"),
    ("image", "Gerar imagem", "todas"), ("transition", "Transição", "video"), ("frame", "Extrair frame", "video"),
    ("review", "Aprovação", "video"), ("montage", "Montagem", "video"),
    ("chargen", "Personagem (ficha + 8 posições)", "character"), ("prodgen", "Produto (ficha + 8 vistas)", "product"),
    ("sheet", "Spritesheet", "character/product"), ("save", "Salvar na biblioteca", "character/product"),
]
NODE_KEYS = [f"node_{t}" for t, _, _ in NODES]
PERM_KEYS = [k for k, _, _ in PERMISSIONS] + NODE_KEYS
BASE = {k: k != "admin" for k in PERM_KEYS}
TOKEN_RATE_DEFAULT = 100  # tokens por US$ 1 (o usuário só vê tokens)


def token_rate() -> float:
    try:
        return max(0.0001, float(db.get_setting("token_rate") or TOKEN_RATE_DEFAULT))
    except (TypeError, ValueError):
        return TOKEN_RATE_DEFAULT


async def atoken_rate() -> float:
    """Versão async de token_rate() para rotas async."""
    try:
        return max(0.0001, float(await db.aget_setting("token_rate") or TOKEN_RATE_DEFAULT))
    except (TypeError, ValueError):
        return TOKEN_RATE_DEFAULT


def to_tokens(usd: float | None) -> float:
    return round((usd or 0) * token_rate(), 2)
WS_PERM = {"video": "ws_video", "character": "ws_character", "product": "ws_product"}
RULE_TYPES = {
    "domain": "Domínio do e-mail (ex.: empresa.com)",
    "entra_group": "ID de grupo do Entra (Object ID)",
    "app_role": "App role do aplicativo (valor)",
    "tenant": "Tenant ID (diretório)",
}
LIMIT_KEYS = ("tokens_month", "storage_mb", "models")

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#10b981',
    permissions TEXT NOT NULL DEFAULT '{}',
    limits TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS user_groups (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual',   -- manual | microsoft
    PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS group_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def init():
    with db._lock:
        db._conn.executescript(SCHEMA)
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(users)")}
        if "auth_provider" not in cols:  # migração: contas vindas da Microsoft
            db._conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
            db._conn.execute("ALTER TABLE users ADD COLUMN ext_id TEXT")
            db._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_ext ON users(auth_provider, ext_id) WHERE ext_id IS NOT NULL")
    _migrate_spend_to_tokens()


def _conv(lim: dict) -> dict:
    if "spend_month_usd" in lim:
        v = lim.pop("spend_month_usd")
        lim.setdefault("tokens_month", None if v is None else int(round(float(v) * token_rate())))
    return lim


def _migrate_spend_to_tokens():
    """Limite de gasto em US$ vira limite em tokens (uma vez, em todo lugar)."""
    d = db.get_setting("default_limits")
    if d and "spend_month_usd" in d:
        db.set_setting("default_limits", _conv(d))
    for table in ("users", "groups"):
        for r in db.all_(f"SELECT id, limits FROM {table} WHERE limits LIKE '%spend_month_usd%'"):
            db.run(f"UPDATE {table} SET limits = ? WHERE id = ?", (json.dumps(_conv(json.loads(r["limits"]))), r["id"]))


# ---------- leitura ----------

def _row(g: dict) -> dict:
    defaults = {k: k in NODE_KEYS for k in PERM_KEYS}  # nodes nunca salvos = liberados
    return {**g, "permissions": {**defaults, **json.loads(g["permissions"] or "{}")},
            "limits": json.loads(g["limits"] or "{}")}


def list_all() -> list[dict]:
    groups = [_row(g) for g in db.all_("SELECT * FROM groups ORDER BY name")]
    members = db.all_("SELECT ug.group_id, ug.user_id, ug.source, u.email, u.name FROM user_groups ug "
                      "JOIN users u ON u.id = ug.user_id ORDER BY u.email")
    rules = db.all_("SELECT * FROM group_rules ORDER BY id")
    for g in groups:
        g["members"] = [m for m in members if m["group_id"] == g["id"]]
        g["rules"] = [r for r in rules if r["group_id"] == g["id"]]
        g["budget_tokens"] = budget_tokens(g["limits"], g["id"]) if g["limits"].get("budget_usd") is not None else None
    return groups


def of_user(uid: int) -> list[dict]:
    return [{**_row(g), "source": g["source"]} for g in db.all_(
        "SELECT g.*, ug.source FROM groups g JOIN user_groups ug ON ug.group_id = g.id WHERE ug.user_id = ? ORDER BY g.name",
        (uid,))]


def _most_generous(values: list):
    if any(v is None for v in values):
        return None
    return max(values)


def member_count(gid: int) -> int:
    return db.one("SELECT COUNT(DISTINCT user_id) AS n FROM user_groups WHERE group_id = ?", (gid,))["n"]


def budget_tokens(lim: dict, gid: int | None) -> int | None:
    """Verba do grupo em US$ -> tokens que cada membro pode gastar no mês."""
    usd = lim.get("budget_usd")
    if usd is None:
        return None
    tok = float(usd) * token_rate()
    if lim.get("budget_split") and gid:
        tok /= max(1, member_count(gid))
    return int(tok)


def apply_budget(g: dict) -> dict:
    if g["limits"].get("budget_usd") is not None:
        g["limits"]["tokens_month"] = budget_tokens(g["limits"], g["id"])
    return g


def effective(uid: int, own_limits: dict, defaults: dict) -> dict:
    gs = [apply_budget(g) for g in of_user(uid)]
    perms = dict(BASE) if not gs else {k: any(g["permissions"].get(k) for g in gs) for k in PERM_KEYS}
    lim = dict(defaults)
    for k in LIMIT_KEYS:
        vals = [g["limits"][k] for g in gs if k in g["limits"]]
        if not vals:
            continue
        if k == "models":
            lim[k] = None if any(v is None for v in vals) else sorted({m for v in vals for m in v})
        else:
            lim[k] = _most_generous(vals)
    lim.update(own_limits)  # limite próprio da conta vence
    return {"permissions": perms, "limits": lim,
            "groups": [{"id": g["id"], "name": g["name"], "color": g["color"], "source": g["source"]} for g in gs]}


# ---------- escrita ----------

def clean_permissions(p: dict) -> dict:
    # node não informado = liberado; permissão base não informada = negada
    return {k: bool(p.get(k, k in NODE_KEYS)) for k in PERM_KEYS}


def save(gid: int | None, name: str, description: str, color: str, permissions: dict, limits: dict) -> int:
    name = name.strip()[:60]
    if not name:
        raise ValueError("Dê um nome ao grupo")
    color = color if isinstance(color, str) and len(color) == 7 and color.startswith("#") else "#10b981"
    other = db.one("SELECT id FROM groups WHERE lower(name) = lower(?)", (name,))
    if other and other["id"] != gid:
        raise ValueError("Já existe um grupo com esse nome")
    args = (name, description.strip()[:300], color, json.dumps(clean_permissions(permissions)), json.dumps(limits))
    if gid:
        db.run("UPDATE groups SET name=?, description=?, color=?, permissions=?, limits=? WHERE id=?", (*args, gid))
        return gid
    return db.run("INSERT INTO groups(name, description, color, permissions, limits, created_at) VALUES(?,?,?,?,?,?)",
                  (*args, time.time()))


def delete(gid: int):
    db.run("DELETE FROM groups WHERE id = ?", (gid,))
    ms = db.get_setting("microsoft") or {}
    if ms.get("default_group_id") == gid:
        db.set_setting("microsoft", {**ms, "default_group_id": None})


def set_manual_members(gid: int, user_ids: list[int]):
    """Membros manuais do grupo (os vindos da Microsoft ficam como estão)."""
    with db.tx() as c:
        c.execute("DELETE FROM user_groups WHERE group_id = ? AND source = 'manual'", (gid,))
        for uid in set(user_ids):
            c.execute("INSERT OR IGNORE INTO user_groups(user_id, group_id, source) VALUES(?,?,'manual')", (uid, gid))


def set_user_manual_groups(uid: int, group_ids: list[int]):
    with db.tx() as c:
        c.execute("DELETE FROM user_groups WHERE user_id = ? AND source = 'manual'", (uid,))
        for gid in set(group_ids):
            c.execute("INSERT OR IGNORE INTO user_groups(user_id, group_id, source) VALUES(?,?,'manual')", (uid, gid))


def add_rule(gid: int, type_: str, value: str) -> int:
    value = value.strip()
    if type_ not in RULE_TYPES or not value:
        raise ValueError("Regra inválida")
    if type_ == "domain":
        value = value.lower().lstrip("@")
    return db.run("INSERT INTO group_rules(group_id, type, value, created_at) VALUES(?,?,?,?)", (gid, type_, value[:200], time.time()))


def delete_rule(rid: int):
    db.run("DELETE FROM group_rules WHERE id = ?", (rid,))


# ---------- atribuição automática ----------

def match_rules(email: str, tenant: str, entra_groups: list[str], app_roles: list[str]) -> set[int]:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    eg = {g.lower() for g in entra_groups}
    roles = set(app_roles)
    out = set()
    for r in db.all_("SELECT group_id, type, value FROM group_rules"):
        v = r["value"]
        if ((r["type"] == "domain" and domain == v.lower()) or (r["type"] == "entra_group" and v.lower() in eg)
                or (r["type"] == "app_role" and v in roles) or (r["type"] == "tenant" and tenant.lower() == v.lower())):
            out.add(r["group_id"])
    return out


def sync_microsoft(uid: int, group_ids: set[int]):
    """Troca os grupos automáticos da conta pelos que bateram agora (os manuais continuam)."""
    with db.tx() as c:
        c.execute("DELETE FROM user_groups WHERE user_id = ? AND source = 'microsoft'", (uid,))
        for gid in group_ids:
            c.execute("INSERT OR IGNORE INTO user_groups(user_id, group_id, source) VALUES(?,?,'microsoft')", (uid, gid))
