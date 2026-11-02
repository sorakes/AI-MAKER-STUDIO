"""Auditoria: nenhum SQL síncrono (db.one/all_/run/tx/...) executado diretamente dentro de código async.

Dentro de `async def`, o acesso ao banco tem de usar a API async (aone/aall/arun/atx) ou ser
delegado a uma thread com asyncio.to_thread (que aparece como referência, não como chamada).
"""
import ast
from pathlib import Path

from app import config

SYNC_DB = {"one", "all_", "run", "tx", "get_setting", "set_setting", "init", "_conn", "_lock"}
APP_DIR = Path(config.BASE_DIR) / "app"


def _direct_calls(fn: ast.AsyncFunctionDef):
    """Chamadas db.<metodo>(...) no corpo da função, ignorando lambdas e funções aninhadas."""
    found = []
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "db" and node.func.attr in SYNC_DB:
            found.append((node.lineno, node.func.attr))
        stack.extend(ast.iter_child_nodes(node))
    return found


def test_no_direct_sync_sql_in_async_code():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for line, attr in _direct_calls(node):
                    offenders.append(f"{path.relative_to(APP_DIR.parent)}:{line} {node.name} -> db.{attr}")
    assert offenders == [], "\n".join(offenders)
