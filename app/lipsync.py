"""Personagem falando: imagem + áudio (ou texto, com voz) -> vídeo com o áudio.

Situação real do provedor (investigado no catálogo público do OpenRouter, ver FORJA9):
- não há entrada de áudio nos modelos de vídeo; alguns geram áudio junto com o vídeo (generate_audio);
- heygen/avatar-iv faz lip sync de uma foto, mas o catálogo só lista voice_id/voice_settings: não sabemos
  se aceita áudio, e não há integração aqui;
- não há endpoint de TTS no OpenRouter (áudio de saída só em modelos de chat/música).

Por isso: `remote()` é o gancho de provedor (simulável nos testes), `synthesize()` é o gancho de voz (sem TTS
configurado, falha com mensagem clara). O caminho que roda hoje é o fallback honesto: animação Ken Burns suave da
imagem com o áudio, via ffmpeg. A UI avisa "sem lip sync real, animação simples".
"""
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import media, providers, registry

MAX_AUDIO_S = 60.0
MIN_AUDIO_S = 0.5
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".opus", ".webm"}
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960)}
FPS = 25


class LipSyncRequest(BaseModel):
    node_id: str = ""
    name: str = "Fala"
    image_url: str  # o personagem (foto ou still)
    audio_url: str | None = None  # áudio enviado (upload)
    script: str = Field("", max_length=1000)  # texto da fala, usado só se houver voz (TTS) configurada
    voice: str | None = None
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    model: str | int | None = "auto"


def pick(user: dict, choice) -> dict | None:
    """Modelo de vídeo com lip sync real; hoje nenhum está habilitado (caps 'lipsync' ausente)."""
    if choice == "local":
        return None
    return registry.pick(user, "video", choice, {"lipsync": True})


def tts_available() -> bool:
    """Sem provedor de voz configurado. Quando houver, este é o lugar de ligar."""
    return False


def synthesize(script: str, voice: str | None) -> bytes:
    if not tts_available():
        raise providers.ProviderError("Sem voz (TTS) configurada: envie um áudio para o personagem falar")
    raise providers.ProviderError("TTS não implementado")


def remote(m: dict, image_url: str, audio_url: str | None, script: str) -> bytes:
    """Gancho do provedor de lip sync. Sem API integrada, falha e o job cai no fallback quando permitido."""
    raise providers.ProviderError(f"{m.get('label') or 'modelo'}: integração de lip sync não configurada")


def _ffprobe(path: Path) -> dict:
    out = subprocess.run([media._ff("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise ValueError("arquivo não lido como mídia")
    return json.loads(out.stdout or "{}")


def audio_info(path: Path) -> dict:
    """Valida um áudio enviado: extensão, trilha de áudio presente e duração dentro do limite."""
    if path.suffix.lower() not in AUDIO_EXT:
        raise ValueError("Formato de áudio não suportado (use mp3, wav, m4a, ogg, flac…)")
    d = _ffprobe(path)
    streams = [s.get("codec_type") for s in d.get("streams", [])]
    duration = round(float(d.get("format", {}).get("duration") or 0), 2)
    if "audio" not in streams or duration <= 0:
        raise ValueError("O arquivo não tem uma trilha de áudio")
    if duration < MIN_AUDIO_S:
        raise ValueError("Áudio curto demais (mínimo de meio segundo)")
    if duration > MAX_AUDIO_S:
        raise ValueError(f"Áudio longo demais (máximo de {int(MAX_AUDIO_S)} segundos)")
    return {"duration": duration}


def kenburns(image: Path, audio: Path, dest: Path, aspect: str, seconds: float) -> Path:
    """Animação suave (zoom lento de 1.0 a 1.08) da imagem, com o áudio por cima. Duração = duração do áudio."""
    w, h = SIZES.get(aspect, SIZES["16:9"])
    frames = max(1, int(round(seconds * FPS)))
    graph = (f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
             f"zoompan=z='min(zoom+{0.08 / frames:.6f},1.08)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
             f"s={w}x{h}:fps={FPS},format=yuv420p[v]")
    cmd = [media._ff("ffmpeg"), "-y", "-loop", "1", "-i", str(image), "-i", str(audio),
           "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
           "-c:a", "aac", "-t", f"{seconds:.2f}", "-shortest", str(dest)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(done.stderr[-600:])
    return dest


def apply_local(op: dict, image: Path, audio: Path, dest: Path) -> Path:
    if op.get("kind") != "lipsync":
        raise ValueError("operação local desconhecida")
    return kenburns(image, audio, dest, op.get("aspect", "16:9"), float(op["seconds"]))
