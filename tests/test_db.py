import threading

import pytest

from app import db


def test_init_is_idempotent():
    db.init()
    db.init()
    assert db.one("PRAGMA journal_mode")["journal_mode"] == "wal"


def test_run_and_read_back():
    db.set_setting("teste.valor", {"a": 1})
    assert db.get_setting("teste.valor") == {"a": 1}
    db.set_setting("teste.valor", [2])
    assert db.get_setting("teste.valor") == [2]
    assert db.get_setting("inexistente", "padrão") == "padrão"


def test_one_and_all_return_dicts():
    db.set_setting("teste.lista.1", 1)
    db.set_setting("teste.lista.2", 2)
    rows = db.all_("SELECT key, value FROM settings WHERE key LIKE 'teste.lista.%' ORDER BY key")
    assert [r["key"] for r in rows] == ["teste.lista.1", "teste.lista.2"]
    assert db.one("SELECT key FROM settings WHERE key = ?", ("nada",)) is None


def test_tx_commits_and_rolls_back():
    with db.tx() as c:
        c.execute("INSERT INTO settings(key, value) VALUES('tx.ok', '1')")
    assert db.one("SELECT value FROM settings WHERE key = 'tx.ok'")["value"] == "1"

    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO settings(key, value) VALUES('tx.falha', '1')")
            raise RuntimeError("boom")
    assert db.one("SELECT 1 FROM settings WHERE key = 'tx.falha'") is None


def test_write_in_one_thread_is_visible_in_another():
    """Leitores são conexões por thread: a escrita confirmada tem de aparecer em qualquer thread."""
    for i in range(20):
        db.set_setting("teste.thread", i)
        seen = {}
        t = threading.Thread(target=lambda: seen.update(v=db.get_setting("teste.thread")))
        t.start()
        t.join()
        assert seen["v"] == i


def test_concurrent_reads_and_writes_do_not_fail():
    errors = []

    def writer():
        try:
            for i in range(300):
                db.run("INSERT INTO settings(key, value) VALUES('teste.carga', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(i),))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(300):
                db.all_("SELECT * FROM settings")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
