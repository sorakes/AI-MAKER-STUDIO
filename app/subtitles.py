"""Legendas automáticas: transcrição (STT via OpenRouter), editor de segmentos, estilos prontos e queima com ffmpeg.

- Transcrição: o áudio do vídeo vai (WAV mono 16 kHz) para um modelo de áudio de chat, o mais barato do catálogo
  com entrada de áudio (google/gemini-2.5-flash-lite). Sem chave ou sem modelo, o usuário digita ou envia .srt/.vtt.
- Estilos: clássico, karaokê (destaque palavra por palavra), caixa alta amarela (shorts) e minimal. Viram ASS.
- Queima: ffmpeg com o filtro `ass`; o .srt sai junto. Fonte: DejaVu (fonts-dejavu-core no Dockerfile).
- Tradução: texto para en/pt/es via o modelo de texto já configurado (barato), em lote.
"""
import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import llm, media, providers

STT_ID = "stt-audio-chat"
STT_MODEL = "google/gemini-2.5-flash-lite"  # entrada de áudio, o mais barato do catálogo público (ver FORJA12)
STT_LOCAL = {"id": STT_ID, "kind": "text", "label": "Transcrição (Gemini Flash Lite)", "provider": "openrouter",
             "model_id": STT_MODEL, "caps": {}, "fields": {}, "params": {}, "cost": 0.001, "cost_unit": "run",
             "key_id": None, "key_name": "", "is_default": 0, "enabled": 1}
MAX_AUDIO_S = 180.0
MAX_SEGMENTS = 300
STYLES = ("classic", "karaoke", "shorts", "minimal")
POSITIONS = ("bottom", "middle", "top")
SIZES = ("small", "medium", "large")
LANGS = ("en", "pt", "es")
HEX = r"#[0-9a-fA-F]{6}"


class Segment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=300)


class TranscribeRequest(BaseModel):
    node_id: str = ""
    name: str = "Legendas"
    source_url: str
    language: str = Field("", max_length=8)  # dica opcional do idioma falado


class SubtitleRequest(BaseModel):
    node_id: str = ""
    name: str = "Legendas"
    source_url: str
    segments: list[Segment] = Field(default_factory=list)
    import_text: str = Field("", max_length=400_000)  # conteúdo de um .srt/.vtt enviado (texto)
    style: Literal["classic", "karaoke", "shorts", "minimal"] = "classic"
    position: Literal["bottom", "middle", "top"] = "bottom"
    size: Literal["small", "medium", "large"] = "medium"
    color: str = Field("#ffffff", pattern=HEX)
    highlight: str = Field("#facc15", pattern=HEX)  # cor da palavra em destaque (karaokê)
    translate_to: Literal["", "en", "pt", "es"] = ""

    @field_validator("segments")
    @classmethod
    def _limit(cls, v):
        if len(v) > MAX_SEGMENTS:
            raise ValueError("muitos segmentos")
        return v


# ---------- tempo, SRT e VTT ----------

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE = re.compile(rf"{_TIME}\s*-->\s*{_TIME}")


def _secs(h, m, s, ms) -> float:
    frac = int((ms or "0").ljust(3, "0")[:3]) / 1000
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + frac


def parse_subs(text: str) -> list[dict]:
    """Lê .srt ou .vtt (mesmo formato de blocos com linha de tempo)."""
    body = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if body.upper().startswith("WEBVTT"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    out = []
    for block in re.split(r"\n\s*\n", body):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            m = _CUE.search(line)
            if not m:
                continue
            a = _secs(*m.group(1, 2, 3, 4))
            b = _secs(*m.group(5, 6, 7, 8))
            content = "\n".join(lines[i + 1:]).strip()
            content = re.sub(r"<[^>]+>", "", content)  # tags de estilo do VTT/SRT
            if content and b > a:
                out.append({"start": round(a, 3), "end": round(b, 3), "text": content})
            break
    return out


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    blocks = [f"{i}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{s['text']}"
              for i, s in enumerate(segments, 1)]
    return "\n\n".join(blocks) + "\n"


def clean_segments(segments: list[dict], duration: float) -> list[dict]:
    """Ordena, corta o que passa do fim do vídeo e descarta o que ficou vazio ou inválido."""
    out = []
    for s in sorted(segments, key=lambda x: x["start"]):
        start = max(0.0, float(s["start"]))
        end = min(float(s["end"]), duration) if duration > 0 else float(s["end"])
        text = str(s.get("text", "")).strip()
        if text and end > start:
            out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    if not out:
        raise ValueError("Nenhum segmento válido: confira os tempos (fim depois do início, dentro do vídeo)")
    return out


# ---------- ASS (estilos) ----------

_SIZE = {"small": 0.8, "medium": 1.0, "large": 1.3}
_STYLE = {
    "classic": dict(font=54, bold=0, outline=3, shadow=1, upper=False),
    "karaoke": dict(font=60, bold=1, outline=3, shadow=1, upper=False),
    "shorts": dict(font=76, bold=1, outline=5, shadow=0, upper=True),
    "minimal": dict(font=42, bold=0, outline=0, shadow=2, upper=False),
}
_ALIGN = {"bottom": 2, "middle": 5, "top": 8}


def _ass_color(hexc: str) -> str:
    r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _karaoke(seg: dict) -> str:
    words = seg["text"].split()
    if not words:
        return ""
    span = seg["end"] - seg["start"]
    per = span / len(words)
    return " ".join(f"{{\\kf{max(1, int(round(per * 100)))}}}{_ass_text(w)}" for w in words)


def to_ass(segments: list[dict], style: str = "classic", position: str = "bottom", size: str = "medium",
           color: str = "#ffffff", highlight: str = "#facc15") -> str:
    st = _STYLE[style]
    fs = int(st["font"] * _SIZE[size])
    align = _ALIGN[position]
    margin = 70 if position != "middle" else 0
    primary = _ass_color(highlight if style == "karaoke" else color)
    secondary = _ass_color(color)
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,DejaVu Sans,{fs},{primary},{secondary},&H00000000,&H64000000,{st['bold']},0,0,0,100,100,0,0,"
        f"1,{st['outline']},{st['shadow']},{align},60,60,{margin},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = []
    for seg in segments:
        text = seg["text"].upper() if st["upper"] else seg["text"]
        body = _karaoke({**seg, "text": text}) if style == "karaoke" else _ass_text(text)
        lines.append(f"Dialogue: 0,{_ass_time(seg['start'])},{_ass_time(seg['end'])},Default,,0,0,0,,{body}")
    return "\n".join(head + lines) + "\n"


# ---------- STT, tradução ----------

def wav_from_video(video: Path, dest: Path) -> float:
    """Áudio mono 16 kHz em WAV (pequeno e simples para o modelo). Devolve a duração do áudio."""
    info = media.probe(video)
    if not info.get("duration"):
        raise ValueError("vídeo sem duração")
    cmd = [media._ff("ffmpeg"), "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
           "-t", str(min(MAX_AUDIO_S, info["duration"])), str(dest)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if done.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        raise ValueError("o vídeo não tem trilha de áudio para transcrever")
    return min(MAX_AUDIO_S, info["duration"])


def stt_call(wav_b64: str, language: str = "") -> str:
    """Chamada ao modelo de áudio (gancho simulável nos testes). Devolve o texto da resposta."""
    hint = f" The speech is in {language}." if language else ""
    messages = [
        {"role": "system", "content": (
            "Transcribe the speech of this audio. Reply ONLY JSON: {\"language\":\"<code>\",\"segments\":"
            "[{\"start\":<seconds>,\"end\":<seconds>,\"text\":\"...\"}]}. Times are seconds from the start; each "
            "segment is a short phrase (at most 8 seconds). Keep the spoken language. If there is no speech, return "
            "an empty list." + hint)},
        {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}}]},
    ]
    msg, _cost = providers.openrouter_chat(STT_MODEL, messages, as_json=True, timeout=180)
    return msg.get("content") or ""


def parse_stt(raw: str) -> dict:
    try:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError as e:
        raise providers.ProviderError("resposta de transcrição ilegível") from e
    segs = [{"start": float(s["start"]), "end": float(s["end"]), "text": str(s["text"]).strip()}
            for s in data.get("segments", []) if str(s.get("text", "")).strip()]
    return {"language": str(data.get("language") or ""), "segments": segs}


def transcribe_wav(wav: Path, language: str = "") -> dict:
    raw = stt_call(base64.b64encode(wav.read_bytes()).decode(), language)
    return parse_stt(raw)


def translate(segments: list[dict], target: str, cost_out: list | None = None) -> list[dict]:
    """Traduz o texto de cada segmento, em lote, mantendo a ordem e os tempos. O custo real vai para cost_out."""
    if target not in LANGS:
        raise ValueError("idioma de destino inválido")
    names = {"en": "English", "pt": "Brazilian Portuguese", "es": "Spanish"}
    texts = [s["text"] for s in segments]
    raw, cost = llm.chat_priced([
        {"role": "system", "content": (f"Translate each subtitle line into {names[target]}. Keep the same number of "
                                       "items and the same order. Reply ONLY JSON: {\"texts\": [...]}.")},
        {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
    ], kind="text", as_json=True, timeout=120)
    if cost_out is not None:
        cost_out.append(cost)
    if not raw:
        raise ValueError("Tradução indisponível: configure um modelo de texto")
    try:
        out = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())["texts"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError("Tradução ilegível: tente de novo") from e
    if len(out) != len(texts):
        raise ValueError("Tradução com número de linhas diferente: tente de novo")
    return [{**s, "text": str(t).strip() or s["text"]} for s, t in zip(segments, out)]


# ---------- queima ----------

def burn(video: Path, op: dict, dest: Path) -> Path:
    """Queima as legendas no vídeo (ffmpeg + libass) e grava o .srt ao lado. Devolve o caminho do .srt."""
    segs = op["segments"]
    ass = dest.with_suffix(".ass")
    srt = dest.with_suffix(".srt")
    ass.write_text(to_ass(segs, op["style"], op["position"], op["size"], op["color"], op["highlight"]), encoding="utf-8")
    srt.write_text(to_srt(segs), encoding="utf-8")
    cmd = [media._ff("ffmpeg"), "-y", "-i", str(video), "-vf", f"ass={ass.name}",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "copy", dest.name]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(dest.parent))
    finally:
        ass.unlink(missing_ok=True)  # o .ass é só o intermediário do ffmpeg: não fica em renders
    if done.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(done.stderr[-600:])
    return srt
