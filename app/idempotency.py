"""Um clique = um job.

Pedidos iguais (mesmo usuário, mesmo nó e mesmo conteúdo) em até 60 s devolvem o job já criado, e um pedido que chega
enquanto o primeiro ainda está sendo criado espera por ele. Assim um clique duplo, ou um reenvio do navegador, não
cria nem cobra o mesmo job duas vezes. O `client_request_id` do front não entra no hash: cada clique manda um id
novo, então é o conteúdo (nó + pedido) que identifica a repetição.
"""
import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

WINDOW = 60.0  # segundos em que um pedido igual é considerado repetição
_cache: dict[tuple, tuple[float, asyncio.Future]] = {}


def key_for(user_id: Any, kind: str, node_id: str, payload: dict) -> tuple:
    body = {k: v for k, v in payload.items() if k != "client_request_id"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return (user_id, kind, node_id, digest)


def _prune(now: float) -> None:
    for k in [k for k, (t, f) in _cache.items() if now - t > WINDOW and f.done()]:
        del _cache[k]


async def once(key: tuple, make: Callable[[], Awaitable[Any]], now: float | None = None) -> Any:
    """Roda `make` uma vez por chave dentro da janela; os repetidos recebem o mesmo resultado (ou o mesmo erro)."""
    now = time.monotonic() if now is None else now
    _prune(now)
    hit = _cache.get(key)
    if hit is not None:
        return await asyncio.shield(hit[1])
    fut = asyncio.get_running_loop().create_future()
    _cache[key] = (now, fut)
    try:
        resp = await make()
    except BaseException as e:  # erro não fica na janela: um novo clique tenta de novo
        _cache.pop(key, None)
        if not fut.done():
            fut.set_exception(e)
        raise
    fut.set_result(resp)
    return resp
