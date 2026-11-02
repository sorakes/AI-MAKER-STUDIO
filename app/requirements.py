"""O que cada recurso (nó) do estúdio exige para funcionar e se está coberto agora. Alimenta o painel admin
(Keys & Modelos → "Modelos obrigatórios por recurso").

Complementa a lista de funções de geração do painel (texto, visão, imagem, vídeo): aqui entram os nós que usam
modelos fixos (que não passam pelo cadastro, ex.: voz, música, transcrição, editores de vídeo) ou que exigem algo
além do modelo (ex.: o Editar vídeo precisa do túnel público).

status: "ok" = funciona com IA agora · "missing" = não funciona, falta algo obrigatório ·
        "fallback" = funciona, mas na versão simples (sem IA) até ter o modelo · "local" = não usa modelo.
"""
from . import audio as sound, performance, providers, registry, subtitles, tunnel, videoedit


def _fits(m: dict, need: dict) -> bool:
    return all(m["caps"].get(k) for k, v in need.items() if v)


def report() -> list[dict]:
    models = registry.list_models()
    live = [m for m in models if m["available"]]
    orkey = providers.available("openrouter")
    tun = tunnel.available()
    rows: list[dict] = []

    def add(key, node, needs, status, using, fix=""):
        rows.append({"key": key, "node": node, "needs": needs, "status": status, "using": using, "fix": fix})

    def ai_or_simple(key, node, needs, kind, need, simple):
        found = [m["label"] for m in live if m["kind"] == kind and _fits(m, need)]
        add(key, node, needs, "ok" if found else "fallback", ", ".join(found[:3]) or simple,
            "" if found else "Adicione um modelo de imagem com referências (ex.: Nano Banana) para a versão com IA.")

    # Editar vídeo: só modelos que RECEBEM o vídeo, pelo link público do túnel
    editors = [c["label"] for c in videoedit.CATALOG] + [
        m["label"] for m in live if m["kind"] == "video" and m["model_id"] in videoedit.VIDEO_INPUT_MODELS]
    missing = [x for x, ok in (("key do OpenRouter", orkey), ("túnel público (serviço 'tunnel' do docker-compose)", tun)) if not ok]
    add("videoedit", "Editar vídeo",
        "Key do OpenRouter + túnel público + um modelo que recebe vídeo (FLUX Video Edit, Runway Aleph e Wan 2.7 já vêm "
        "prontos; Seedance 2.x entra se estiver cadastrado). Modelos só texto/imagem → vídeo não servem.",
        "ok" if not missing else "missing", ", ".join(editors),
        f"Falta: {' e '.join(missing)}." if missing else "")

    # Áudio: modelos fixos do OpenRouter (não passam pelo cadastro)
    for key, node, mid in (("speech", "Áudio · Voz", sound.SPEECH_MODEL), ("music", "Áudio · Música", sound.MUSIC_MODEL)):
        add(key, node, f"Key do OpenRouter (modelo fixo {mid}, não precisa cadastrar).", "ok" if orkey else "missing",
            mid, "" if orkey else "Cadastre a key do OpenRouter.")
    add("sfx", "Áudio · Efeito", "Nada: o efeito é sintetizado no próprio servidor (ffmpeg).", "local", "ffmpeg")

    add("subtitles", "Legendas",
        f"Key do OpenRouter para transcrever (modelo fixo {subtitles.STT_MODEL}, não precisa cadastrar). A queima das "
        "legendas é local. Legendas importadas (.srt) não precisam de modelo.", "ok" if orkey else "missing",
        subtitles.STT_MODEL, "" if orkey else "Cadastre a key do OpenRouter.")

    perf = next((m for m in live if m["model_id"] == performance.PERF_MODEL_ID), None)
    add("performance", "Performance", f"O modelo {performance.PERF_MODEL_ID} cadastrado, ativo e com key (fala com voz própria).",
        "ok" if perf else "missing", perf["label"] if perf else "",
        "" if perf else f"Adicione {performance.PERF_MODEL_ID} em Modelos disponíveis.")

    lips = [m["label"] for m in live if m["kind"] == "video" and m["caps"].get("lipsync")]
    add("lipsync", "Fala (lip sync)", "Um modelo de vídeo com lip sync. Sem ele, o nó faz uma animação simples com o áudio.",
        "ok" if lips else "fallback", ", ".join(lips[:3]) or "animação simples (sem IA)",
        "" if lips else "Adicione um modelo de vídeo com lip sync para a fala com IA.")

    for key, node in (("upscale", "Upscale"), ("outpaint", "Expandir / editar região"), ("relight", "Relight / Estilo")):
        ai_or_simple(key, node, "Um modelo de imagem com referências. Sem ele, o nó usa a versão simples (sem IA).",
                     "image", {"refs": True}, "versão simples (sem IA)")
    return rows
