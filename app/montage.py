"""Montagem final com ffmpeg: clipes com ponto de corte + transições (xfade) + trilha/locução."""
import subprocess
import uuid
from pathlib import Path

from . import storage
from .config import MONTAGE_FPS
from .media import _ff, probe

RESOLUTIONS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960)}

TRANSITIONS = {
    "cut": {"label": "Corte seco", "xfade": "fade", "default": 0.05},
    "dissolve": {"label": "Dissolve", "xfade": "fade", "default": 0.6},
    "fade_black": {"label": "Fade pelo preto", "xfade": "fadeblack", "default": 0.8},
    "flash": {"label": "Flash branco", "xfade": "fadewhite", "default": 0.4},
    "whip_right": {"label": "Whip pan →", "xfade": "smoothleft", "default": 0.35},
    "whip_left": {"label": "Whip pan ←", "xfade": "smoothright", "default": 0.35},
    "zoom_through": {"label": "Zoom através", "xfade": "zoomin", "default": 0.5},
    "blur": {"label": "Desfoque", "xfade": "hblur", "default": 0.5},
    "wipe": {"label": "Wipe", "xfade": "wipeleft", "default": 0.5},
    "iris": {"label": "Íris", "xfade": "circleopen", "default": 0.6},
    "match_cut": {"label": "Match cut (frame contínuo)", "xfade": "fade", "default": 0.05},
    "bridge": {"label": "Ponte gerada por IA", "xfade": "fade", "default": 0.05},
}
MIN_T = 1 / MONTAGE_FPS


def assemble_sequence(uid: int, items: list[dict], aspect: str = "16:9", audio: Path | None = None) -> Path:
    """items: [{type:"clip", path, start?, end?}, {type:"transition", kind, duration}, ...]"""
    clips, joins, pending = [], [], None
    for it in items:
        if it.get("type") == "transition":
            pending = it
            continue
        if clips:
            joins.append(pending or {"kind": "cut"})
        clips.append(it)
        pending = None
    if not clips:
        raise ValueError("nenhum vídeo pronto para montar")

    w, h = RESOLUTIONS.get(aspect, RESOLUTIONS["16:9"])
    args, chains, durs = [_ff("ffmpeg"), "-y"], [], []
    for i, c in enumerate(clips):
        path: Path = c["path"]
        full = probe(path)["duration"] or 5
        start = max(0.0, float(c.get("start") or 0))
        end = min(full, float(c.get("end") or full))
        if end - start < 0.3:
            start, end = 0.0, full
        durs.append(end - start)
        args += ["-i", str(path)]
        chains.append(
            f"[{i}:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={MONTAGE_FPS},setsar=1,"
            f"format=yuv420p,settb=AVTB[v{i}]")

    cur, total = "v0", durs[0]
    if joins and all(j.get("kind", "cut") == "cut" for j in joins):
        # só cortes secos: concatena sem sobreposição, para o total bater com a soma dos trechos (o xfade tirava
        # um pedaço de cada corte e o áudio ficava fora de sincronia)
        chains.append("".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[vout]")
        cur, total = "vout", sum(durs)
    else:
        for i, j in enumerate(joins, start=1):
            spec = TRANSITIONS.get(j.get("kind", "cut"), TRANSITIONS["cut"])
            d = float(j.get("duration") or spec["default"])
            d = max(MIN_T, min(d, durs[i - 1] - 0.1, durs[i] - 0.1))
            offset = max(0.0, total - d)
            chains.append(f"[{cur}][v{i}]xfade=transition={spec['xfade']}:duration={d:.3f}:offset={offset:.3f}[x{i}]")
            cur, total = f"x{i}", total + durs[i] - d

    if audio:
        args += ["-i", str(audio)]
    args += ["-filter_complex", ";".join(chains), "-map", f"[{cur}]"]
    if audio:
        args += ["-map", f"{len(clips)}:a", "-c:a", "aac", "-af", "apad", "-t", f"{total:.3f}"]
    out = storage.user_dir(uid, "cuts") / f"corte_{uuid.uuid4().hex[:6]}.mp4"
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-900:])
    except BaseException:  # falha ou timeout: não deixa corte parcial ocupando disco
        out.unlink(missing_ok=True)
        raise
    return out
