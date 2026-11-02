"""Áudio: voz (TTS pelo gpt-audio-mini), música (Lyria, por prompt) e efeitos (biblioteca sintetizada com ffmpeg).

Voz e música são modelos reais do OpenRouter (cobrança real, registrada no job). Efeitos são sintetizados aqui:
não são gravações de verdade, e isso aparece no node.
"""
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import media, providers

SPEECH_MODEL = "openai/gpt-audio-mini"      # saída de áudio barata; o áudio vem em PCM 16 bits, 24 kHz, mono
MUSIC_MODEL = "google/lyria-3-clip-preview"  # música por prompt; devolve MP3 (a cobrança real foi US$ 0,04 por clipe)
VOICES = ("alloy", "nova", "onyx", "shimmer")
MAX_TEXT = 500
SPEECH_RATE = 24000
MUSIC_SECONDS = (5, 30)
SFX_SECONDS = (1, 10)
SFX_KINDS = ("rain", "wind", "whoosh", "beep")
GENRES = ("pop", "rock", "electronic", "jazz", "classical", "lofi", "hiphop", "acoustic")
MOODS = ("happy", "epic", "calm", "tense", "romantic", "mysterious")
GENS = ("speech", "music", "sfx")
LABELS = {"speech": "Voz", "music": "Música", "sfx": "Efeito"}

# modelos "virtuais" do job: o provedor real é o OpenRouter (voz/música) ou o próprio ffmpeg (efeito)
SPEECH_LOCAL = {"id": "audio-speech", "kind": "audio", "label": "Voz (gpt-audio-mini)", "provider": "openrouter",
                "model_id": SPEECH_MODEL, "caps": {}, "fields": {}, "params": {}, "cost": 0.001, "cost_unit": "run",
                "key_id": None, "key_name": "", "is_default": 0, "enabled": 1}
MUSIC_LOCAL = {"id": "audio-music", "kind": "audio", "label": "Música (Lyria)", "provider": "openrouter",
               "model_id": MUSIC_MODEL, "caps": {}, "fields": {}, "params": {}, "cost": 0.04, "cost_unit": "run",
               "key_id": None, "key_name": "", "is_default": 0, "enabled": 1}
SFX_LOCAL = {"id": "audio-sfx", "kind": "audio", "label": "Efeito (sintetizado)", "provider": "ffmpeg",
             "model_id": "lavfi", "caps": {}, "fields": {}, "params": {}, "cost": 0.0, "cost_unit": "run",
             "key_id": None, "key_name": "", "is_default": 0, "enabled": 1}
VIRTUAL = {m["id"]: m for m in (SPEECH_LOCAL, MUSIC_LOCAL, SFX_LOCAL)}
GEN_MODEL = {"speech": SPEECH_LOCAL, "music": MUSIC_LOCAL, "sfx": SFX_LOCAL}


class AudioError(Exception):
    pass


class SpeechRequest(BaseModel):
    node_id: str = ""
    name: str = "Áudio"
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    voice: Literal["alloy", "nova", "onyx", "shimmer"] = "alloy"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class MusicRequest(BaseModel):
    node_id: str = ""
    name: str = "Música"
    prompt: str = Field(default="", max_length=300)
    genre: Literal["pop", "rock", "electronic", "jazz", "classical", "lofi", "hiphop", "acoustic"] = "pop"
    mood: Literal["happy", "epic", "calm", "tense", "romantic", "mysterious"] = "happy"
    seconds: int = Field(default=10, ge=MUSIC_SECONDS[0], le=MUSIC_SECONDS[1])


class SfxRequest(BaseModel):
    node_id: str = ""
    name: str = "Efeito"
    kind: Literal["rain", "wind", "whoosh", "beep"] = "rain"
    seconds: float = Field(default=3.0, ge=SFX_SECONDS[0], le=SFX_SECONDS[1])


def speech_seconds(text: str) -> float:
    """Estimativa para a cotação antes de gerar (~14 caracteres por segundo de fala)."""
    return round(max(1.0, len(text) / 14), 1)


def speech_messages(text: str) -> list[dict]:
    return [{"role": "system", "content": "You are a voice actor. Speak the user's text exactly as written, in its own "
                                          "language. No introduction, no translation, no comments."},
            {"role": "user", "content": text}]


def music_prompt(req: MusicRequest) -> str:
    extra = f" {req.prompt.strip()}" if req.prompt.strip() else ""
    return (f"{req.genre} {req.mood} instrumental background music, about {req.seconds} seconds, no vocals.{extra}")


def _ff(args: list[str]) -> None:
    r = subprocess.run([media._ff("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise AudioError(f"ffmpeg falhou: {r.stderr.strip()[-300:]}")


def pcm_to_mp3(pcm: Path, dest: Path, speed: float = 1.0) -> None:
    """PCM 16 bits, 24 kHz, mono (saída da voz) -> MP3. A velocidade é ajuste de tempo do ffmpeg (0,5× a 2×)."""
    args = ["-f", "s16le", "-ar", str(SPEECH_RATE), "-ac", "1", "-i", str(pcm)]
    if abs(speed - 1.0) > 0.01:
        args += ["-af", f"atempo={speed:.2f}"]
    _ff([*args, "-c:a", "libmp3lame", "-b:a", "128k", str(dest)])


def trim_mp3(src: Path, dest: Path, seconds: int) -> None:
    """Corta o clipe no tamanho pedido, com fade-out no fim (o Lyria devolve um clipe de tamanho fixo)."""
    fade = max(0.0, seconds - 1.0)
    _ff(["-i", str(src), "-t", str(seconds), "-af", f"afade=t=out:st={fade}:d=1", "-c:a", "libmp3lame",
         "-b:a", "128k", str(dest)])


def _sfx_source(kind: str, s: float) -> tuple[str, str]:
    """(fonte lavfi, filtro) para cada efeito sintetizado."""
    if kind == "rain":  # ruído rosa filtrado: chuva constante
        return f"anoisesrc=color=pink:amplitude=0.25:duration={s}", "highpass=f=300,lowpass=f=5000"
    if kind == "wind":  # ruído marrom com lenta modulação: vento
        return f"anoisesrc=color=brown:amplitude=0.6:duration={s}", "lowpass=f=700,tremolo=f=0.25:d=0.5"
    if kind == "whoosh":  # ruído branco em faixa estreita, subindo e caindo: passagem rápida
        h = round(s / 2, 3)
        return (f"anoisesrc=color=white:amplitude=0.7:duration={s}",
                f"bandpass=f=1200:width_type=h:w=1000,afade=t=in:d={h},afade=t=out:st={h}:d={h}")
    if kind == "beep":  # senoide curta
        return f"sine=frequency=880:duration={s}", f"volume=0.4,afade=t=out:st={max(0.0, s - 0.05):.3f}:d=0.05"
    raise AudioError("efeito desconhecido")


def synth_sfx(kind: str, seconds: float, dest: Path) -> None:
    src, filt = _sfx_source(kind, seconds)
    _ff(["-f", "lavfi", "-i", src, "-af", filt, "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "128k", str(dest)])


def run(gen: str, spec, dest: Path, m: dict) -> float:
    """Gera o arquivo do job (voz, música ou efeito) e devolve o custo real em US$."""
    key_id = m.get("key_id")
    tmp = dest.with_suffix(".tmp")
    try:
        if gen == "speech":
            data, _text, cost = providers.openrouter_audio(SPEECH_MODEL, speech_messages(spec.prompt),
                                                           audio={"voice": spec.local_op["voice"], "format": "pcm16"},
                                                           key_id=key_id)
            if not data:
                raise AudioError("O modelo não devolveu áudio para esse texto")
            tmp.write_bytes(data)
            pcm_to_mp3(tmp, dest, float(spec.local_op.get("speed", 1.0)))
            return cost
        if gen == "music":
            data, _text, cost = providers.openrouter_audio(MUSIC_MODEL, [{"role": "user", "content": spec.prompt}],
                                                           key_id=key_id)
            if not data:
                raise AudioError("O modelo não devolveu música")
            tmp.write_bytes(data)
            trim_mp3(tmp, dest, int(spec.local_op["seconds"]))
            return cost
        if gen == "sfx":
            synth_sfx(spec.local_op["kind"], float(spec.local_op["seconds"]), dest)
            return 0.0
    finally:
        tmp.unlink(missing_ok=True)
    raise AudioError("tipo de áudio desconhecido")


def duration(path: Path) -> float:
    return media.audio_duration(path)
