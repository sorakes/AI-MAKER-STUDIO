"""Um clique = um job: pedidos iguais em 60 s devolvem o mesmo job; erro não fica guardado; o id do clique não conta."""
import asyncio

import pytest

from app import idempotency as idem


def test_key_is_per_user_node_and_content_and_ignores_the_click_id():
    a = idem.key_for(1, "inpaint", "n1", {"prompt": "x", "client_request_id": "clique-a"})
    b = idem.key_for(1, "inpaint", "n1", {"prompt": "x", "client_request_id": "clique-b"})
    assert a == b  # clique duplo: ids diferentes, mesmo pedido
    assert a != idem.key_for(1, "inpaint", "n2", {"prompt": "x"})  # outro nó
    assert a != idem.key_for(1, "inpaint", "n1", {"prompt": "y"})  # outro conteúdo (ex.: 'Tentar de novo' reforçado)
    assert a != idem.key_for(2, "inpaint", "n1", {"prompt": "x"})  # outro usuário
    assert a != idem.key_for(1, "outpaint", "n1", {"prompt": "x"})  # outro tipo de pedido


def test_same_request_twice_at_the_same_time_creates_one_job():
    calls = []

    async def make():
        calls.append(1)
        await asyncio.sleep(0.01)
        return {"id": "job-1"}

    async def run():
        k = idem.key_for(1, "inpaint", "node-concorrente", {"p": 1})
        return await asyncio.gather(idem.once(k, make, now=100.0), idem.once(k, make, now=100.5))

    assert asyncio.run(run()) == [{"id": "job-1"}, {"id": "job-1"}]
    assert len(calls) == 1


def test_after_the_window_the_same_request_creates_a_new_job():
    calls = []

    async def make():
        calls.append(1)
        return {"id": f"job-{len(calls)}"}

    async def run():
        k = idem.key_for(1, "inpaint", "node-janela", {"p": 2})
        first = await idem.once(k, make, now=100.0)
        later = await idem.once(k, make, now=100.0 + idem.WINDOW + 1)
        return first, later

    first, later = asyncio.run(run())
    assert first["id"] == "job-1" and later["id"] == "job-2"


def test_a_failed_request_is_not_kept_and_the_next_click_tries_again():
    state = {"n": 0}

    async def make():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("cota esgotada")
        return {"id": "job-ok"}

    async def run():
        k = idem.key_for(1, "inpaint", "node-erro", {"p": 3})
        with pytest.raises(RuntimeError):
            await idem.once(k, make, now=10.0)
        return await idem.once(k, make, now=10.5)

    assert asyncio.run(run()) == {"id": "job-ok"}
    assert state["n"] == 2
