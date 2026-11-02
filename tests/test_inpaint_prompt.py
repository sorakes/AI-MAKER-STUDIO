"""Prompt do inpaint: remoção vs substituição, a região marcada como única área a mudar e reforço na nova tentativa."""
from app import outpaint as o


def _req(text):
    return o.InpaintRequest(source_url="/files/1/x.png", mask="data:image/png;base64,AA==", prompt=text)


def test_removal_intent_is_detected_in_portuguese_and_english():
    assert o.is_removal("remova oq está circulado")
    assert o.is_removal("remove the wings")
    assert o.is_removal("tire isso")
    assert o.is_removal("")  # sem texto = remover o que foi marcado
    assert not o.is_removal("troque o céu por pôr do sol")


def test_removal_prompt_asks_to_remove_and_fill_the_background():
    p = o.inpaint_prompt(_req("remova oq está circulado"))
    assert "Remove the object or part inside the magenta region" in p
    assert "fill that area with the surrounding background" in p
    assert "Replace the content inside the magenta region" not in p


def test_replacement_prompt_keeps_the_user_text_as_the_new_content():
    p = o.inpaint_prompt(_req("troque o céu por pôr do sol"))
    assert "Replace the content inside the magenta region with: troque o céu por pôr do sol" in p


def test_every_prompt_says_the_region_is_the_only_change():
    for text in ("remova", "troque o céu", ""):
        p = o.inpaint_prompt(_req(text))
        assert "ONLY area to change" in p and "pixel-identical" in p and "NO magenta" in p


def test_retry_prompt_is_stronger():
    base = o.inpaint_prompt(_req("remova"))
    retry = o.inpaint_prompt(_req("remova"), reinforce=True)
    assert retry != base and "MUST visibly change" in retry and "MUST visibly change" not in base


def test_change_stats_flags_an_unchanged_region():
    from PIL import Image
    orig = Image.new("RGB", (64, 64), (100, 100, 100))
    mask = Image.new("L", (64, 64), 0)
    mask.paste(255, (10, 10, 40, 40))
    same = o.change_stats(orig, orig.copy(), mask)
    assert same["mask_pixels"] > 0 and same["unchanged"] is True and same["outside_mean"] == 0.0
    changed = orig.copy()
    changed.paste((200, 30, 30), (10, 10, 40, 40))
    assert o.change_stats(orig, changed, mask)["unchanged"] is False
