"""Registro de uso (logs do painel) e limites por usuário."""
import datetime as dt
import json

from fastapi import HTTPException

from . import db
from .auth import CURRENT


_LOG_SQL = "INSERT INTO usage(ts, user_id, kind, provider, model, cost, status, detail) VALUES(?,?,?,?,?,?,?,?)"


def _log_args(kind: str, provider, model, cost, status, detail, user_id) -> tuple:
    if user_id is None:
        u = CURRENT.get()
        user_id = u["id"] if u else None
    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)
    return (db.now(), user_id, kind, provider, model, float(cost or 0), status, (detail or "")[:2000])


def log(kind: str, *, provider: str | None = None, model: str | None = None, cost: float = 0.0,
        status: str = "ok", detail: str | dict | None = None, user_id: int | None = None):
    db.run(_LOG_SQL, _log_args(kind, provider, model, cost, status, detail, user_id))


async def alog(kind: str, *, provider: str | None = None, model: str | None = None, cost: float = 0.0,
               status: str = "ok", detail: str | dict | None = None, user_id: int | None = None):
    """Versão async (aiosqlite) de log(): para uso dentro de rotas e tarefas async."""
    await db.arun(_LOG_SQL, _log_args(kind, provider, model, cost, status, detail, user_id))


def month_start() -> float:
    d = dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp()


def month_stats(user_id: int) -> dict:
    rows = db.all_("SELECT kind, COUNT(*) n, COALESCE(SUM(cost), 0) c FROM usage "
                   "WHERE user_id = ? AND ts >= ? AND status = 'ok' GROUP BY kind", (user_id, month_start()))
    by = {r["kind"]: r for r in rows}
    return {"images": by.get("image", {}).get("n", 0), "videos": by.get("video", {}).get("n", 0),
            "texts": by.get("text", {}).get("n", 0) + by.get("vision", {}).get("n", 0),
            "spend": round(sum(r["c"] for r in rows), 4)}


class LimitError(HTTPException):
    def __init__(self, msg: str):
        super().__init__(402, msg)


def check(user: dict, *, images: int = 0, videos: int = 0, est_cost: float = 0.0, model_ids: list[int] = (),
          pending: dict | None = None):
    """pending = renders ainda em andamento (contam como já gastos, senão dá para estourar em paralelo)."""
    lim = user["limits"]
    s = month_stats(user["id"])
    for k in ("images", "videos", "spend"):
        s[k] += (pending or {}).get(k, 0)
    allowed = lim.get("models")
    if allowed is not None and any(m not in allowed for m in model_ids):
        raise LimitError("Modelo não liberado para sua conta")
    if lim.get("tokens_month") is not None:
        from .groups import to_tokens
        used, need = to_tokens(s["spend"]), to_tokens(est_cost)
        if used + need > lim["tokens_month"]:
            raise LimitError(f"Seus tokens do mês acabaram: usados {used:.0f} de {lim['tokens_month']}, "
                             f"este render precisa de ~{max(1, round(need))}. Fale com o administrador.")
