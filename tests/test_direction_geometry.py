"""Desenho de direção: a seta só existe na câmera; elementos usam ponto cheio (início) e anel vazio (fim)."""
import inspect

from app import direction, jobs


def _layer(color, shapes):
    return direction.Layer(color=color, shapes=[direction.Shape(type=t, points=p) for t, p in shapes])


PATH = [["path", [[0.2, 0.2], [0.8, 0.5]]]]
LOOP = [["loop", [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]]]]


def test_camera_line_describes_arrow_at_end():
    g = direction._geometry(_layer("camera", PATH))
    assert "dot = start" in g and "arrow = end" in g
    assert "ring" not in g


def test_element_line_describes_solid_dot_and_hollow_ring():
    g = direction._geometry(_layer("yellow", PATH))
    assert "solid dot = start" in g and "hollow ring = end" in g
    assert "arrow" not in g


def test_loops_keep_their_description():
    for color in ("camera", "yellow"):
        g = direction._geometry(_layer(color, LOOP))
        assert g.startswith("circled area at the ")
        assert "arrow" not in g and "ring" not in g


def test_empty_or_single_point_shapes_have_no_text():
    assert direction._geometry(_layer("yellow", [["path", [[0.1, 0.1]]]])) == ""


def test_vision_prompt_uses_arrow_only_for_camera():
    s = direction.SYSTEM
    assert "PURPLE is the CAMERA" in s and "arrow = end" in s
    assert "solid DOT" in s and "hollow RING" in s
    elements_part = s.split("Every OTHER color is")[1].split("The drawing itself")[0]  # só a descrição dos elementos
    assert "arrow" not in elements_part


def test_video_prompt_text_matches_new_geometry():
    src = inspect.getsource(jobs)
    assert "a line from a solid dot to a hollow ring" in src
    assert "(line/arrow)" not in src
