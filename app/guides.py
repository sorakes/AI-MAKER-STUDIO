"""Guias de pose: manequim (personagem) ou caixa (produto) desenhados em 3D simples em cada ângulo.

Vão escondidos como última referência de cada posição, para o modelo de imagem entender o ÂNGULO
exato sem copiar a aparência. Renderização própria (PIL), sem custo e sempre igual.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

from .config import DATA_DIR

GUIDE_DIR = DATA_DIR / "guides"
SIZE = 768
BG = (226, 228, 232)

# Rotação (graus) em torno do eixo vertical: para onde o corpo aponta na imagem.
# 0 = de frente; negativo = vira para a ESQUERDA da imagem (mesma convenção dos prompts).
ANGLES = {"front": 0, "front_right": -45, "right": -90, "back_right": -135, "back": 180,
          "back_left": 135, "left": 90, "front_left": 45}


def _rot(p, th):
    x, y, z = p
    c, s = math.cos(th), math.sin(th)
    return (x * c + z * s, y, -x * s + z * c)


def _shade(base, z, zmax):
    k = 0.72 + 0.28 * (z + zmax) / (2 * zmax)
    return tuple(max(0, min(255, int(v * k))) for v in base)


def _mannequin(key: str) -> Image.Image:
    th = math.radians(ANGLES[key])
    J = {
        "head": (0, 7.25, 0), "neck": (0, 6.55, 0), "chest": (0, 5.75, 0), "pelvis": (0, 4.15, 0),
        "shL": (0.9, 6.25, 0), "shR": (-0.9, 6.25, 0), "elL": (1.1, 5.05, -0.05), "elR": (-1.1, 5.05, -0.05),
        "haL": (1.15, 3.95, 0.12), "haR": (-1.15, 3.95, 0.12), "hiL": (0.48, 4.05, 0), "hiR": (-0.48, 4.05, 0),
        "knL": (0.52, 2.15, 0.08), "knR": (-0.52, 2.15, 0.08), "anL": (0.52, 0.35, 0), "anR": (-0.52, 0.35, 0),
        "toL": (0.56, 0.12, 0.62), "toR": (-0.56, 0.12, 0.62),
    }
    base, dark, accent = (168, 168, 172), (55, 58, 66), (96, 128, 196)
    prims = [  # (pontos, raio, cor)
        (("neck", "chest"), 0.26, base), (("chest", "pelvis"), 0.78, base), (("shL", "shR"), 0.42, base),
        (("hiL", "hiR"), 0.46, base),
        (("shL", "elL"), 0.24, base), (("elL", "haL"), 0.2, base), (("shR", "elR"), 0.24, base), (("elR", "haR"), 0.2, base),
        (("hiL", "knL"), 0.3, base), (("knL", "anL"), 0.25, base), (("hiR", "knR"), 0.3, base), (("knR", "anR"), 0.25, base),
        (("anL", "toL"), 0.2, dark), (("anR", "toR"), 0.2, dark),  # pés apontam para a frente: mostram a direção
        (("head",), 0.62, base),
        (((0, 5.95, 0.62),), 0.32, accent),  # peito: só aparece de frente
        (((0, 7.18, 0.6),), 0.14, base),  # nariz
        (((0.24, 7.38, 0.5),), 0.08, dark), (((-0.24, 7.38, 0.5),), 0.08, dark),  # olhos
        (((0, 6.9, -0.55),), 0.34, (130, 130, 136)),  # nuca/cabelo: marca as costas
    ]
    pts = {k: _rot(v, th) for k, v in J.items()}
    scale = SIZE * 0.84 / 8.0
    cx, ground = SIZE / 2, SIZE * 0.93
    proj = lambda p: (cx + p[0] * scale, ground - p[1] * scale)  # noqa: E731
    items = []
    for ps, r, col in prims:
        P = [pts[p] if isinstance(p, str) else _rot(p, th) for p in ps]
        z = sum(p[2] for p in P) / len(P)
        items.append((z, P, r, col))
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - 1.6 * scale, ground - 0.25 * scale, cx + 1.6 * scale, ground + 0.25 * scale], fill=(206, 208, 212))
    for z, P, r, col in sorted(items, key=lambda t: t[0]):
        c = _shade(col, z, 1.2)
        rr = r * scale
        q = [proj(p) for p in P]
        if len(q) == 2:
            d.line([q[0], q[1]], fill=c, width=int(2 * rr))
        for x, y in q:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c)
    return img


def _box(key: str) -> Image.Image:
    w, h, dd = 1.4, 2.6, 0.9
    V = [(sx * w, sy * h, sz * dd) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    faces = {  # índices dos vértices, normal
        "front": ([1, 3, 7, 5], (0, 0, 1)), "back": ([0, 4, 6, 2], (0, 0, -1)), "right": ([4, 5, 7, 6], (1, 0, 0)),
        "left": ([0, 2, 3, 1], (-1, 0, 0)), "top": ([2, 6, 7, 3], (0, 1, 0)), "bottom": ([0, 1, 5, 4], (0, -1, 0)),
    }
    colors = {"front": (96, 128, 196), "back": (150, 150, 156), "right": (172, 172, 176), "left": (160, 160, 166),
              "top": (196, 196, 200), "bottom": (120, 120, 126)}
    th = math.radians(ANGLES.get(key, 0))
    tilt = math.radians(90 if key == "top" else 12)  # positivo = câmera um pouco acima

    def tr(p):
        x, y, z = _rot(p, th)
        c, s = math.cos(tilt), math.sin(tilt)
        return (x, y * c - z * s, y * s + z * c)
    scale = SIZE * 0.6 / (2 * h)
    proj = lambda p: (SIZE / 2 + p[0] * scale, SIZE / 2 - p[1] * scale)  # noqa: E731
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    vis = []
    for name, (idx, nrm) in faces.items():
        n = tr(nrm)
        if n[2] > 0.02:
            P = [tr(V[i]) for i in idx]
            vis.append((sum(p[2] for p in P) / 4, name, P))
    for _, name, P in sorted(vis):
        d.polygon([proj(p) for p in P], fill=colors[name], outline=(80, 82, 90))
        if name == "front":  # "rótulo" na frente
            a, b, c2, e = P
            lerp = lambda p, q, t: tuple(p[i] + (q[i] - p[i]) * t for i in range(3))  # noqa: E731
            lab = [lerp(lerp(a, b, .25), lerp(e, c2, .25), .3), lerp(lerp(a, b, .25), lerp(e, c2, .25), .7),
                   lerp(lerp(a, b, .75), lerp(e, c2, .75), .7), lerp(lerp(a, b, .75), lerp(e, c2, .75), .3)]
            d.polygon([proj(p) for p in lab], fill=(236, 236, 240))
    return img


def guide_path(kind: str, key: str) -> Path | None:
    """Caminho da guia (gera e guarda na primeira vez). None = sem guia para esta vista."""
    if kind == "character" and key not in ANGLES:
        return None
    if kind == "product" and key not in ANGLES and key != "top":
        return None
    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    p = GUIDE_DIR / f"{kind}_{key}_v2.png"
    if not p.exists():
        (_mannequin(key) if kind == "character" else _box(key)).save(p)
    return p
