"""Node Estender / Loop removido: sem rota, sem paleta, sem tipo de nó; canvas antigo carrega sem quebrar."""
import re

from app import config
from app.main import app

JS = config.STATIC_DIR / "js"


def _text(p):
    return p.read_text(encoding="utf-8")


def test_routes_are_gone():
    paths = {r.path for r in app.routes}
    assert "/api/video/extend" not in paths and "/api/video/loop" not in paths


def test_palette_and_kind_no_longer_have_the_node():
    src = _text(JS / "canvas.js")
    ws = re.search(r"const WS_PALETTE = \{(.*?)\};", src, re.S).group(1)
    assert "'extend'" not in ws
    assert "  extend: {" not in src and "extend: extendBody" not in src and "newExtend" not in src
    assert "startExtendJob" not in src and "runLoop" not in src


def test_legacy_nodes_are_pruned_when_a_canvas_loads():
    src = _text(JS / "canvas.js")
    assert "const REMOVED_NODE_TYPES = ['extend'];" in src
    assert "function pruneRemovedNodes(d)" in src
    load = src.split("async function loadWorkspaceDoc(ws) {")[1][:900]
    assert "pruneRemovedNodes(d)" in load
    assert "cv.prunedNotice" in load  # aviso discreto, uma vez por sessão


def test_pruning_also_drops_the_edges_of_removed_nodes():
    src = _text(JS / "canvas.js")
    prune = src.split("function pruneRemovedNodes(d) {")[1].split("\n}\n")[0]
    assert "d.edges = d.edges.filter" in prune and "gone.has(e.from)" in prune and "gone.has(e.to)" in prune


def test_shared_helpers_stay_for_other_features():
    from app import media
    assert callable(media.has_audio) and callable(media.extract_frame)
    assert callable(__import__("app.montage", fromlist=["x"]).assemble_sequence)
