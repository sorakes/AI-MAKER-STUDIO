"""Storyboard no canvas: botão e script na página, endpoint, ligações dos nós, confirmação de custo, sem inline e i18n."""
import re

from app import config

STATIC = config.STATIC_DIR
JS = STATIC / "js"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_storyboard_button_and_script_are_on_the_studio_page():
    html = _text(STATIC / "studio.html")
    assert 'id="cvStoryboard"' in html
    assert html.index("/js/creation.js") < html.index("/js/storyboard.js")  # usa helpers de creation.js


def test_plan_comes_from_the_endpoint_and_nothing_is_generated_before_confirmation():
    src = _text(JS / "storyboard.js")
    assert "'/api/storyboard/plan'" in src
    create = src.split("function createStoryboard")[1]
    assert "if (generate && !dlg.querySelector('[data-sb=confirm]').checked) return" in create
    assert "if (generate) runAll()" in create  # gerar tudo só depois da confirmação
    assert "[data-sb=create]').disabled = !ok || blocked" in src  # botão travado enquanto o custo não é confirmado


def test_nodes_are_linked_image_to_video_to_cut_with_cast_on_ref_port():
    create = _text(JS / "storyboard.js").split("function createStoryboard")[1]
    assert "cv.edges.push({ id: uid('e'), from: img.id, to: vid.id })" in create  # imagem -> vídeo
    assert "port: 'ref'" in create and "to: img.id" in create and "to: vid.id" in create  # elenco na porta R
    assert "type: 'montage'" in create and "from: vid.id, to: mont.id" in create  # cada vídeo vai para o corte


def test_cast_reuses_inventory_by_name_and_creates_empty_nodes_otherwise():
    src = _text(JS / "storyboard.js")
    assert "a.name.toLowerCase() === key" in src  # inventário pelo nome
    assert "assetId: asset?.id || ''" in src and "desc: asset ? '' : desc" in src  # nó vazio leva a descrição


def test_camera_comes_from_the_editor_presets():
    src = _text(JS / "storyboard.js")
    assert "state.catalog?.camera?.moves" in src  # mesmos presets do app/camera.py
    assert "s.camera_hint = moves[el.value]?.prompt" in src


def test_no_inline_styles_in_the_storyboard_code():
    src = _text(JS / "storyboard.js")
    assert 'style="' not in src and "style='" not in src
    assert "el.style" not in src and ".style." not in src


def test_english_and_spanish_cover_the_dialog_labels():
    en = _text(JS / "i18n-en.js")
    es = _text(JS / "i18n-es.js")
    for label in ("Briefing", "Planejar", "Só criar os nós (custo zero)", "Gerar tudo agora",
                  "Criar storyboard no canvas", "Confirmo o custo estimado"):
        assert f'"{label}"' in en or f"'{label}'" in en, label
    for label in ("Storyboard", "Plan", "Only create the nodes (no cost)", "Generate everything now"):
        assert re.search(rf'"{re.escape(label)}"', es), label
