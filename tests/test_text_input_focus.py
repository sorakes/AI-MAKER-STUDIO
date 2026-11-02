"""Foco nos campos de texto: um handler de 'input' de texto/range não pode reconstruir o node (renderNode).
Reconstruir recria o DOM e o campo perde o foco a cada tecla. Só seletores e campos estruturais podem reconstruir."""
import re

from app import config

JS = config.STATIC_DIR / "js"
# condições que tornam a reconstrução aceitável: seletor, troca de modo/operação ou estilo (mudam o layout do node)
STRUCTURAL = re.compile(r"SELECT|'preset'|'op'|\"op\"|'style'|'mode'|'tool'|'kind'|'factor'|'duration'")
INPUT_ADD = re.compile(r"addEventListener\((?:'input'|ev)\s*,")


def _call_block(src, start):
    """Texto de uma chamada addEventListener(...) completa: do '(' até o ')' que a fecha."""
    depth = 0
    for j in range(src.index("(", start), len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return src[start:]


def _violations():
    bad = []
    for f in sorted(JS.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        for m in INPUT_ADD.finditer(src):
            block = _call_block(src, m.start())
            for r in re.finditer(r"renderNode\(", block):
                before = block[:r.start()].rstrip().splitlines()[-1].strip() if block[:r.start()].strip() else ""
                if not STRUCTURAL.search(before):
                    lineno = src.count("\n", 0, m.start()) + 1
                    bad.append(f"{f.name}:{lineno} -> {before[:90]}")
                    break
    return bad


def test_no_text_input_handler_rebuilds_the_node():
    bad = _violations()
    assert bad == [], "handlers de texto que reconstroem o node (perdem o foco):\n" + "\n".join(bad)


def test_fala_textarea_does_not_rebuild_the_node():
    src = (JS / "canvas.js").read_text(encoding="utf-8")
    speak = src.split("function bindSpeak(el, n) {")[1].split("el.querySelector('[data-role=speak-audio]')")[0]
    assert "else syncSpeakButton(el, n);" in speak
    assert "function syncSpeakButton" in src


def test_performance_text_fields_do_not_rebuild_the_node():
    src = (JS / "performance.js").read_text(encoding="utf-8")
    assert "else syncPerformanceButtons(el, n);" in src
    assert "if (inp.tagName === 'SELECT') renderNode(n);" in src


def test_video_edit_number_fields_do_not_rebuild_the_node():
    src = (JS / "videoedit.js").read_text(encoding="utf-8")
    assert "else syncVideoEditState(el, n);" in src and 'data-role="vedit-span"' in src
