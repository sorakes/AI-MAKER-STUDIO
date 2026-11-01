"""Mídia: metadados (ffprobe), frames (ffmpeg) e conversão para data URI."""
import base64
import io
import json
import mimetypes
import shutil
import subprocess
import uuid
from pathlib import Path

from PIL import Image

from . import storage


def _ff(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"{name} não encontrado no PATH")
    return exe


def probe(path: Path) -> dict:
    out = subprocess.run([_ff("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30)
    d = json.loads(out.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    return {"width": s.get("width"), "height": s.get("height"),
            "duration": round(float(d.get("format", {}).get("duration", 0) or 0), 2)}


def extract_frame(video: Path, last: bool) -> Path:
    dest = video.with_name(f"{video.stem}_{'last' if last else 'first'}.png")
    if not dest.exists():
        args = [_ff("ffmpeg"), "-y"] + (["-sseof", "-0.1"] if last else []) + \
               ["-i", str(video), "-frames:v", "1", "-update", "1", str(dest)]
        subprocess.run(args, capture_output=True, timeout=60, check=True)
    return dest


def extract_at(video: Path, t: float) -> Path:
    """Frame no tempo t (s): ponto de corte/continuidade."""
    dest = video.with_name(f"{video.stem}_t{int(round(t * 1000)):06d}.png")
    if not dest.exists():
        subprocess.run([_ff("ffmpeg"), "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                        "-update", "1", str(dest)], capture_output=True, timeout=60, check=True)
        if not dest.exists():  # t além do fim: último frame
            return extract_frame(video, last=True)
    return dest


def safe_name(filename: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-60:] or "arquivo"


def save_upload(uid: int, filename: str, data: bytes) -> dict:
    path = storage.user_dir(uid, "uploads") / f"{uuid.uuid4().hex[:8]}_{safe_name(filename)}"
    path.write_bytes(data)
    if storage.kind_of(path) == "file":
        path.unlink()
        raise ValueError("Formato não suportado (use imagem, vídeo ou áudio)")
    return describe(path)


def describe(path: Path) -> dict:
    kind = storage.kind_of(path)
    url = storage.to_url(path)
    if kind == "video":
        info = probe(path)
        return {"kind": "video", "url": url, "thumb": storage.to_url(extract_frame(path, last=False)),
                "last_frame": storage.to_url(extract_frame(path, last=True)), "name": path.name, **info}
    if kind == "audio":
        return {"kind": "audio", "url": url, "name": path.name}
    if path.suffix.lower() == ".gif":  # GIF animado pesado: miniatura parada (1º quadro)
        return {"kind": "image", "url": url, "thumb": storage.to_url(gif_thumb(path)), "name": path.name}
    return {"kind": "image", "url": url, "thumb": url, "name": path.name}


def gif_thumb(path: Path) -> Path:
    dest = path.with_name(f"{path.stem}_first.png")
    if not dest.exists():
        from PIL import Image
        with Image.open(path) as im:
            im.seek(0)
            im = im.convert("RGB")
            im.thumbnail((480, 480))
            im.save(dest)
    return dest


def data_url(path: Path, max_side: int = 2048) -> str:
    """Imagem -> data URI. Imagens grandes são reduzidas (pedido menor, mesmo resultado)."""
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if len(raw) > 3 * 2**20:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        raw, mime = buf.getvalue(), "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
