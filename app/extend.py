"""Estender vídeo (continuação com IA a partir do último frame) e loop perfeito (sem IA, ffmpeg).

- Estender: o último frame do clipe vira o quadro inicial de um vídeo novo (modelo com start frame). Depois o
  clipe original e a continuação são unidos pelo montage (corte seco ou dissolve curto).
- Loop: ida e volta (o clipe, depois o mesmo clipe ao contrário) ou crossfade final->início (o fim se funde
  no começo, então o último frame encosta no primeiro). Só ffmpeg, sem modelo.
"""
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import direction, media, registry

NO_START_MSG = ("Nenhum modelo de vídeo com quadro inicial disponível para estender. "
                "Use o Loop (sem IA), que funciona sem modelo.")


class ExtendRequest(BaseModel):
    node_id: str = ""
    name: str = "Estender"
    source_url: str  # clipe original (/files/...)
    seconds: Literal[3, 5, 8] = 5
    transition: Literal["none", "dissolve"] = "none"  # como unir original e continuação
    prompt: str = Field("", max_length=1000)
    direction_layers: list[direction.Layer] = Field(default_factory=list)  # câmera/movimento do editor de direção
    model: str | int | None = "auto"


class LoopRequest(BaseModel):
    node_id: str = ""
    name: str = "Loop"
    source_url: str
    mode: Literal["pingpong", "crossfade"] = "pingpong"
    seconds: Literal[0.5, 1.0, 1.5] = 1.0  # duração do crossfade (só no modo crossfade)
    model: str | int | None = "auto"


def pick(user: dict, choice) -> dict | None:
    """Modelo de vídeo que aceita quadro inicial (start frame): é o que a continuação precisa."""
    return registry.pick(user, "video", choice, {"i2v": True})


def direction_text(layers: list[direction.Layer]) -> str:
    """Câmera e movimentos desenhados, em texto (o mesmo que o editor de direção descreve)."""
    parts = []
    for l in layers:
        geo = direction._geometry(l)
        if l.color == "camera":
            parts.append(f"Camera: {l.prompt.strip() or 'follow the drawn route'}"
                         + (f" ({geo})" if geo else "") + ".")
        elif geo or l.prompt.strip():
            parts.append(f"{direction.COLOR_NAMES.get(l.color, l.color).capitalize()} element: "
                         f"{l.prompt.strip() or 'moves as drawn'}" + (f" ({geo})" if geo else "") + ".")
    return " ".join(parts)


def extend_prompt(req: ExtendRequest) -> str:
    """A continuação começa exatamente no último frame (imagem 1) e segue o movimento pedido."""
    move = direction_text(req.direction_layers)
    note = f" {req.prompt.strip()}" if req.prompt.strip() else ""
    return (f"Continue this shot seamlessly for {req.seconds} seconds, starting exactly from the first frame. "
            f"Keep the same subject, lighting, colors and style. {move}{note} No cuts, no titles, no marks.").replace("  ", " ")


def aspect_of(path: Path) -> str:
    """Aspecto do clipe original, entre os formatos que o app gera."""
    info = media.probe(path)
    w, h = info.get("width") or 16, info.get("height") or 9
    r = w / h
    return "9:16" if r < 0.8 else "1:1" if r < 1.2 else "16:9"


def has_audio(path: Path) -> bool:
    out = subprocess.run([media._ff("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30)
    return any(s.get("codec_type") == "audio" for s in json.loads(out.stdout or "{}").get("streams", []))


def loop_graph(mode: str, duration: float, seconds: float, audio: bool) -> tuple[str, list[str]]:
    """Grafo ffmpeg do loop e os rótulos de saída a mapear. Função pura (testada sem ffmpeg)."""
    if mode == "pingpong":
        g = "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0[v]"
        maps = ["[v]"]
        if audio:
            g += ";[0:a]asplit[aa][ab];[ab]areverse[ar];[aa][ar]concat=n=2:v=0:a=1[a]"
            maps.append("[a]")
        return g, maps
    d = seconds
    end = duration
    # split antes de usar a mesma entrada duas vezes (ffmpeg não aceita [0:v] consumido duas vezes).
    # Sem setpts após o trim: com timestamps zerados o xfade não recebe quadros (testado no ffmpeg 8).
    g = (f"[0:v]split[va][vb];"
         f"[va]trim=start={end - d:.3f}:end={end:.3f}[t];"
         f"[vb]trim=start=0:end={end - d:.3f}[m];"
         f"[t][m]xfade=transition=fade:duration={d:.3f}:offset=0,setpts=PTS-STARTPTS[v]")
    maps = ["[v]"]
    if audio:
        # som: a cauda sai enquanto o começo entra (amix). acrossfade não produz áudio neste grafo (testado).
        g += (f";[0:a]asplit[aa][ab];"
              f"[aa]atrim=start={end - d:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,afade=t=out:st=0:d={d:.3f}[ta];"
              f"[ab]atrim=start=0:end={end - d:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={d:.3f}[ma];"
              f"[ta][ma]amix=inputs=2:duration=longest:normalize=0[a]")
        maps.append("[a]")
    return g, maps


def apply_loop(op: dict, src: Path, dest: Path) -> Path:
    """Gera o loop no ffmpeg e grava em `dest` (mp4)."""
    if op.get("kind") != "loop":
        raise ValueError("operação local desconhecida")
    duration = media.probe(src)["duration"]
    if duration <= 0:
        raise ValueError("clipe sem duração")
    seconds = float(op.get("seconds", 1.0))
    if op["mode"] == "crossfade" and seconds >= duration / 2:
        raise ValueError("crossfade maior que a metade do clipe")
    audio = has_audio(src)
    graph, maps = loop_graph(op["mode"], duration, seconds, audio)
    cmd = [media._ff("ffmpeg"), "-y", "-i", str(src), "-filter_complex", graph]
    for m in maps:
        cmd += ["-map", m]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(dest))
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(done.stderr[-600:])
    return dest
