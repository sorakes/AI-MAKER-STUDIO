"""Qualidade do inpaint: recomposição com borda suave, prompt semântico e fallback sem visão."""
from PIL import Image

from app import llm, outpaint as o


def _solid(path, size, value):
    Image.new("RGB", size, (value, value, value)).save(path)
    return path


def _rect_mask(path, size, box):
    m = Image.new("L", size, 0)
    m.paste(255, box)
    m.save(path)
    return path


def test_feather_is_smooth_and_identical_outside(tmp_path):
    size = (200, 200)
    orig = _solid(tmp_path / "orig.png", size, 100)
    ai = _solid(tmp_path / "ai.png", size, 200)
    mask = _rect_mask(tmp_path / "mask.png", size, (60, 60, 140, 140))
    out = Image.open(o.finish_inpaint(ai, orig, mask, size, tmp_path / "out.png")).convert("RGB")
    assert out.size == size
    # fora da máscara dilatada: o original, pixel a pixel
    assert out.getpixel((5, 5)) == (100, 100, 100)
    assert out.getpixel((195, 195)) == (100, 100, 100)
    # dentro: a região gerada, com a cor ajustada ao entorno (não cru 200)
    assert 150 <= out.getpixel((100, 100))[0] <= 175
    # na borda: valores intermediários e sem salto brusco entre pixels vizinhos (sem serrilhado)
    row = [out.getpixel((x, 100))[0] for x in range(40, 161)]
    assert any(100 < v < 170 for v in row)
    assert max(abs(a - b) for a, b in zip(row, row[1:])) <= 20


def test_feather_resizes_ai_output_with_other_dimension(tmp_path):
    size = (200, 200)
    orig = _solid(tmp_path / "orig.png", size, 100)
    ai = _solid(tmp_path / "ai.png", (100, 80), 200)  # a IA devolveu outro tamanho
    mask = _rect_mask(tmp_path / "mask.png", size, (60, 60, 140, 140))
    out = Image.open(o.finish_inpaint(ai, orig, mask, size, tmp_path / "out.png")).convert("RGB")
    assert out.size == size
    assert out.getpixel((5, 5)) == (100, 100, 100)


def test_prompt_removal_with_identification_is_semantic_and_has_no_magenta():
    ident = {"target": "the bee's wings", "type": "object", "surroundings": "the bee's body and a blurred green background"}
    p = o.inpaint_prompt(o.InpaintRequest(source_url="/f.png", mask="x", prompt="remova oq está circulado"), ident=ident)
    assert p.startswith("Remove the bee's wings from this photo.")
    assert "the bee's body and a blurred green background" in p
    assert "magenta" not in p.lower() and "circulado" not in p.lower()


def test_prompt_replacement_with_identification():
    ident = {"target": "the bee's wings", "type": "object", "surroundings": "a blurred background"}
    p = o.inpaint_prompt(o.InpaintRequest(source_url="/f.png", mask="x", prompt="troque por um girassol"), ident=ident)
    assert p.startswith("Replace the bee's wings with: troque por um girassol.")
    assert "magenta" not in p.lower()


def test_prompt_reinforce_and_clean_mask_variant():
    ident = {"target": "the bee's wings", "type": "object", "surroundings": "the background"}
    req = o.InpaintRequest(source_url="/f.png", mask="x", prompt="remova", input_mode="clean_mask")
    p = o.inpaint_prompt(req, reinforce=True, ident=ident)
    assert p.startswith("Image 2 is a black-and-white mask")
    assert "must be completely gone" in p


def test_prompt_marked_variant_keeps_magenta_as_only_clue():
    ident = {"target": "the bee's wings", "type": "object", "surroundings": "the background"}
    p = o.inpaint_prompt(o.InpaintRequest(source_url="/f.png", mask="x", prompt="remova", input_mode="marked"), ident=ident)
    assert "MAGENTA" in p and "the bee's wings" in p


def test_prompt_fallback_without_vision_uses_magenta_and_no_user_text():
    p = o.inpaint_prompt(o.InpaintRequest(source_url="/f.png", mask="x", prompt="remova oq está circulado"), ident=None)
    assert "MAGENTA" in p and "Remove the object or part inside the magenta region" in p
    assert "circulado" not in p


def test_identify_falls_back_when_vision_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sem modelo de visão")
    monkeypatch.setattr(llm, "chat_priced", boom)
    img = Image.new("RGB", (60, 60), (10, 10, 10))
    mask = Image.new("L", (60, 60), 0)
    mask.paste(255, (20, 20, 40, 40))
    assert o.identify_region(img, mask) == (None, 0.0)


def test_identify_parses_json_and_rejects_bad_answers(monkeypatch):
    img = Image.new("RGB", (60, 60), (10, 10, 10))
    mask = Image.new("L", (60, 60), 0)
    mask.paste(255, (20, 20, 40, 40))
    monkeypatch.setattr(llm, "chat_priced", lambda *a, **k: ('{"target": "the bee\'s wings", "type": "object", '
                                                              '"surroundings": "the body"}', 0.002))
    assert o.identify_region(img, mask) == ({"target": "the bee's wings", "type": "object", "surroundings": "the body"}, 0.002)
    monkeypatch.setattr(llm, "chat_priced", lambda *a, **k: ("não é json", 0.001))
    assert o.identify_region(img, mask) == (None, 0.001)
    monkeypatch.setattr(llm, "chat_priced", lambda *a, **k: ('{"type": "object"}', 0.001))
    assert o.identify_region(img, mask) == (None, 0.001)
