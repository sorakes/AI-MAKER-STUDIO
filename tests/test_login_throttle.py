"""Rate limit de login persistido em SQLite (tabela login_attempts)."""
import time

import pytest
from fastapi import HTTPException

from app import auth, db


def test_attempts_are_stored_in_database(client):
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "persistido@test.local", "password": "x"})
    rows = db.all_("SELECT key FROM login_attempts WHERE key LIKE '%persistido@test.local'")
    assert len(rows) == 3


def test_limit_is_enforced_after_max_attempts(client):
    codes = [client.post("/api/auth/login", json={"email": "bloq@test.local", "password": "x"}).status_code
             for _ in range(auth.LOGIN_MAX + 1)]
    assert codes[-1] == 429
    assert all(c == 401 for c in codes[:-1])


def test_limit_survives_a_restart_of_the_process(client):
    # simula outro worker/processo: o estado mora no banco, não numa variável
    db.run("DELETE FROM login_attempts")
    now = time.time()
    db.run("INSERT INTO login_attempts(key, ts) VALUES(?, ?)", ("1.2.3.4|reinicio@test.local", now))
    for _ in range(auth.LOGIN_MAX - 1):
        db.run("INSERT INTO login_attempts(key, ts) VALUES(?, ?)", ("1.2.3.4|reinicio@test.local", now))
    with pytest.raises(HTTPException) as e:
        auth._throttle("1.2.3.4|reinicio@test.local")
    assert e.value.status_code == 429


def test_expired_attempts_are_purged_and_do_not_count(client):
    old = time.time() - auth.LOGIN_WINDOW_S - 60
    for _ in range(auth.LOGIN_MAX + 5):
        db.run("INSERT INTO login_attempts(key, ts) VALUES(?, ?)", ("9.9.9.9|antigo@test.local", old))
    auth._throttle("9.9.9.9|antigo@test.local")  # não estoura: as antigas expiraram
    assert db.one("SELECT COUNT(*) n FROM login_attempts WHERE key = ?", ("9.9.9.9|antigo@test.local",))["n"] == 1


def test_successful_login_clears_attempts(make_user, client):
    make_user("limpa@test.local")
    client.post("/api/auth/login", json={"email": "limpa@test.local", "password": "errada"})
    assert db.one("SELECT COUNT(*) n FROM login_attempts WHERE key LIKE '%limpa@test.local'")["n"] == 1
    assert client.post("/api/auth/login", json={"email": "limpa@test.local", "password": "senha-123"}).status_code == 200
    assert db.one("SELECT COUNT(*) n FROM login_attempts WHERE key LIKE '%limpa@test.local'")["n"] == 0
