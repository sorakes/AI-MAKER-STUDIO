"""API async do db (aiosqlite): leitura, escrita, transação atômica e convivência com o caminho síncrono."""
import asyncio
import threading

import pytest

from app import db


@pytest.fixture(autouse=True)
def _clean_keys():
    db.run("DELETE FROM settings WHERE key LIKE 'adb.%'")
    yield


def test_async_write_then_sync_read_in_thread():
    async def write():
        await db.arun("INSERT INTO settings(key, value) VALUES('adb.x', '1')")
    asyncio.run(write())
    seen = {}
    t = threading.Thread(target=lambda: seen.update(v=db.one("SELECT value FROM settings WHERE key='adb.x'")))
    t.start()
    t.join()
    assert seen["v"]["value"] == "1"


def test_async_reads_return_dicts():
    async def go():
        await db.arun("INSERT INTO settings(key, value) VALUES('adb.a', '1')")
        await db.arun("INSERT INTO settings(key, value) VALUES('adb.b', '2')")
        rows = await db.aall("SELECT key FROM settings WHERE key LIKE 'adb.%' ORDER BY key")
        one = await db.aone("SELECT value FROM settings WHERE key = ?", ("adb.b",))
        missing = await db.aone("SELECT value FROM settings WHERE key = ?", ("nada",))
        return rows, one, missing
    rows, one, missing = asyncio.run(go())
    assert [r["key"] for r in rows] == ["adb.a", "adb.b"]
    assert one == {"value": "2"} and missing is None


def test_async_transaction_rolls_back_on_error():
    async def go():
        with pytest.raises(RuntimeError):
            async with db.atx() as conn:
                await conn.execute("INSERT INTO settings(key, value) VALUES('adb.tx', '1')")
                raise RuntimeError("falha")
    asyncio.run(go())
    assert db.one("SELECT 1 FROM settings WHERE key = 'adb.tx'") is None


def test_async_transaction_commits_all_or_nothing():
    async def go():
        async with db.atx() as conn:
            await conn.execute("INSERT INTO settings(key, value) VALUES('adb.c1', '1')")
            await conn.execute("INSERT INTO settings(key, value) VALUES('adb.c2', '2')")
    asyncio.run(go())
    assert len(db.all_("SELECT key FROM settings WHERE key IN ('adb.c1','adb.c2')")) == 2


def test_concurrent_async_writers_do_not_fail():
    async def writer(n):
        for i in range(50):
            await db.arun("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (f"adb.w{n}", str(i)))

    async def main():
        await asyncio.gather(*(writer(n) for n in range(5)))
        return await db.aall("SELECT key FROM settings WHERE key LIKE 'adb.w%'")
    assert len(asyncio.run(main())) == 5


def test_each_event_loop_gets_its_own_connection():
    async def ident():
        ac = await db._aget()
        return id(ac)
    assert asyncio.run(ident()) != asyncio.run(ident())
