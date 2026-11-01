"""SQLite: usuários, sessões, provedores (keys cifradas), modelos, uso, jobs, canvas."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    active INTEGER NOT NULL DEFAULT 1,
    limits TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    key_enc TEXT,
    key_hint TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    key_enc TEXT NOT NULL,
    key_hint TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- text | vision | image | video
    provider TEXT NOT NULL,          -- openrouter | fal
    model_id TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    caps TEXT NOT NULL DEFAULT '{}',   -- t2i, refs, t2v, i2v, end_frame, durations...
    fields TEXT NOT NULL DEFAULT '{}', -- schema de entrada (fal) já resumido
    params TEXT NOT NULL DEFAULT '{}', -- parâmetros fixos enviados sempre (admin)
    cost REAL NOT NULL DEFAULT 0,      -- custo estimado em USD
    cost_unit TEXT NOT NULL DEFAULT 'run', -- run | second
    created_at REAL NOT NULL,
    UNIQUE(provider, model_id, kind)
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id INTEGER,
    kind TEXT NOT NULL,       -- text | vision | image | video | upload | delete | login | montage
    provider TEXT,
    model TEXT,
    cost REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    detail TEXT
);
CREATE INDEX IF NOT EXISTS usage_user_ts ON usage(user_id, ts);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created REAL NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created);
CREATE TABLE IF NOT EXISTS canvases (
    user_id INTEGER PRIMARY KEY,
    doc TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


_conn = _connect()
_conn.execute("PRAGMA journal_mode = WAL")


@contextmanager
def tx():
    with _lock:
        _conn.execute("BEGIN")
        try:
            yield _conn
            _conn.execute("COMMIT")
        except Exception:
            _conn.execute("ROLLBACK")
            raise


def init():
    with _lock:
        _conn.executescript(SCHEMA)
        # migração: um canvas por área do estúdio (video | character | product)
        ccols = {r[1] for r in _conn.execute("PRAGMA table_info(canvases)")}
        if "ws" not in ccols:
            _conn.executescript("""
                CREATE TABLE canvases_v2 (user_id INTEGER NOT NULL, ws TEXT NOT NULL DEFAULT 'video', doc TEXT NOT NULL,
                                          updated REAL NOT NULL, PRIMARY KEY (user_id, ws));
                INSERT INTO canvases_v2(user_id, ws, doc, updated) SELECT user_id, 'video', doc, updated FROM canvases;
                DROP TABLE canvases;
                ALTER TABLE canvases_v2 RENAME TO canvases;
            """)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(models)")}
        if "key_id" not in cols:  # migração: modelo pode usar uma key específica
            _conn.execute("ALTER TABLE models ADD COLUMN key_id INTEGER")
        # migração: key única antiga (tabela providers) vira a key "Principal"
        for r in _conn.execute("SELECT id, key_enc, key_hint FROM providers WHERE key_enc IS NOT NULL").fetchall():
            if not _conn.execute("SELECT 1 FROM api_keys WHERE provider = ?", (r[0],)).fetchone():
                _conn.execute("INSERT INTO api_keys(provider, name, key_enc, key_hint, is_default, created_at) VALUES(?,?,?,?,1,?)",
                              (r[0], "Principal", r[1], r[2], time.time()))
            _conn.execute("UPDATE providers SET key_enc = NULL, key_hint = NULL WHERE id = ?", (r[0],))


def all_(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    with _lock:
        r = _conn.execute(sql, args).fetchone()
        return dict(r) if r else None


def run(sql: str, args=()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        return cur.lastrowid or cur.rowcount


def get_setting(key: str, default=None):
    r = one("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key: str, value):
    run("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)))


def now() -> float:
    return time.time()
