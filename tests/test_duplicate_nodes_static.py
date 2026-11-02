"""Clique duplo e nós repetidos: o front bloqueia o 2º clique, manda um id por clique, mescla cópias ao carregar,
e as duas rotas de edição passam pelo dedupe do servidor."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_front_blocks_a_second_click_while_the_edit_is_in_flight():
    src = _read("static/js/canvas.js")
    assert "const editInFlight = new Set();" in src
    assert "if (editInFlight.has(n.id) || n.status === 'running') return;" in src
    assert "editInFlight.delete(n.id);" in src  # liberado só quando o job termina ou falha


def test_each_click_sends_a_request_id_that_the_server_accepts():
    src = _read("static/js/canvas.js")
    assert src.count("client_request_id: requestId") == 2  # inpaint e outpaint
    outpaint = _read("app/outpaint.py")
    assert outpaint.count('client_request_id: str | None = Field(None, max_length=64)') == 2


def test_loading_a_canvas_merges_nodes_that_share_a_job():
    src = _read("static/js/canvas.js")
    assert "function dedupeJobNodes(d)" in src
    assert "  dedupeJobNodes(d);" in src  # chamado ao carregar o canvas


def test_both_edit_routes_go_through_the_server_dedupe():
    router = _read("app/routers/studio.py")
    assert 'idempotency.once(idempotency.key_for(user["id"], "inpaint"' in router
    assert 'idempotency.once(idempotency.key_for(user["id"], "outpaint"' in router


def test_media_nodes_of_the_same_job_file_merge_across_extensions():
    src = _read("static/js/canvas.js")
    assert "function jobKeyOf(n)" in src and "file:${m[1]}" in src  # .jpg bruto e .png final: mesmo job
