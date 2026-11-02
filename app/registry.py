"""Modelos disponíveis nos nós do estúdio (configurados no painel admin).

Tudo pelo OpenRouter: o catálogo público dá o tipo, as capacidades (texto→vídeo, imagem→vídeo,
frame final, durações) e o preço; o que aceita referências vem do teste real (tabelas abaixo).
"""
import json

from . import db, providers


SEED = [
    # (kind, provider, model_id, label, default, cost, unit)
    ("text", "openrouter", "deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash", 1, 0.0, "run"),
    ("vision", "openrouter", "google/gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", 1, 0.0, "run"),
    ("image", "openrouter", "google/gemini-3.1-flash-image", "Gemini 3.1 Flash Image", 1, 0.04, "run"),
]


# Vídeo do OpenRouter que RESPEITA input_references (testado com 2 imagens: cena + produto) -> máximo de imagens.
# Esses modelos não misturam 1º frame com referências: o frame vai como imagem 1 da lista.
# Teste de 01/11/2026: todos estes usaram as 2 imagens. Máximo = documentação de cada família (só 2 foi testado).
# Recusaram (HTTP 400): minimax/hailuo-3-max, google/veo-3.1-lite, x-ai/grok-imagine-video-1.5-lite, tbc/mischief-1.
OR_VIDEO_REFS: dict[str, int] = {
    "bytedance/seedance-2.0": 9, "bytedance/seedance-2.0-fast": 9, "bytedance/seedance-2.0-mini": 9,
    "bytedance/seedance-2.5": 9, "bytedance/seedance-1-5-pro": 4,
    "kwaivgi/kling-video-o1": 7, "kwaivgi/kling-v3.0-std": 7, "kwaivgi/kling-v3.0-pro": 7,
    "alibaba/wan-2.7": 5, "alibaba/wan-3.0": 5, "alibaba/wan-3.0-prime": 5, "alibaba/wan-2.6": 4,
    "alibaba/happyhorse-1.1": 4,
    "x-ai/grok-imagine-video": 7,
    "runway/gen-4.5": 3,
    "minimax/hailuo-2.3": 2,
    "google/veo-3.1-fast": 3, "google/veo-3.1": 3,  # referência→vídeo só com 8 s
}
# Aceitam, mas no teste não respeitaram tudo (cena + produto + texto): ficam fora dos recomendados.
OR_VIDEO_REFS_WEAK = {
    "alibaba/wan-2.6": "muda o produto da referência",
    "alibaba/wan-2.7": "não começa na cena da imagem 1",
    "runway/gen-4.5": "troca a cena da imagem 1",
    "x-ai/grok-imagine-video": "erra o tamanho do produto e ignora parte do texto",
    "bytedance/seedance-1-5-pro": "pode colar o fundo da referência no vídeo",
    "minimax/hailuo-2.3": "erra o tamanho do produto",
    "google/veo-3.1-fast": "não testado (só 8 s)", "google/veo-3.1": "não testado (só 8 s)",
}
# Imagem (chat do OpenRouter): todas as referências vão na mesma mensagem. Máximo pela documentação de cada modelo.
OR_IMAGE_REFS = {
    "google/gemini-3-pro-image": 14, "google/gemini-3-pro-image-preview": 14,
    "google/gemini-3.1-flash-image": 14, "google/gemini-3.1-flash-image-preview": 14,
    "google/gemini-nano-banana-2.1": 14, "google/gemini-3.1-flash-lite-image": 6,
    "google/gemini-2.5-flash-image": 3,
    "openai/gpt-5-image": 16, "openai/gpt-5-image-mini": 16, "openai/gpt-5.4-image-2": 16,
}
OR_VIDEO_REFS_DURATIONS = {"google/veo-3.1-fast": [8], "google/veo-3.1": [8]}


def _row(r: dict) -> dict:
    m = {**r, "caps": json.loads(r["caps"]), "fields": json.loads(r["fields"]), "params": json.loads(r["params"]),
         "enabled": bool(r["enabled"]), "is_default": bool(r["is_default"])}
    if m["kind"] == "video" and m["fields"].get("api") == "openrouter_video":
        mx = OR_VIDEO_REFS.get(m["model_id"], 0)
        m["caps"]["refs"] = mx > 0  # os outros recebem só 1º/último frame (referências são descartadas)
        m["caps"]["max_refs"] = mx
        if mx and m["model_id"] in OR_VIDEO_REFS_WEAK:
            m["caps"]["refs_weak"] = OR_VIDEO_REFS_WEAK[m["model_id"]]
    elif m["kind"] == "image" and m["caps"].get("refs"):
        m["caps"]["max_refs"] = OR_IMAGE_REFS.get(m["model_id"], 4)
    m["available"] = m["enabled"] and providers.available(m["provider"], m.get("key_id"))
    m["key_name"] = providers.key_name(m["provider"], m.get("key_id"))
    return m


def list_models(kind: str | None = None) -> list[dict]:
    # só provedores ativos no app (modelos antigos do fal ficam no banco, ocultos)
    ph = ",".join("?" * len(providers.PROVIDERS))
    sql, args = f"SELECT * FROM models WHERE provider IN ({ph})", tuple(providers.PROVIDERS)
    if kind:
        sql, args = sql + " AND kind = ?", (*args, kind)
    return [_row(r) for r in db.all_(sql + " ORDER BY kind, is_default DESC, label", args)]


def get(mid: int) -> dict | None:
    r = db.one("SELECT * FROM models WHERE id = ?", (mid,))
    return _row(r) if r else None


async def aget(mid: int) -> dict | None:
    r = await db.aone("SELECT * FROM models WHERE id = ?", (mid,))
    return _row(r) if r else None


def public(m: dict) -> dict:
    """O que o estúdio precisa saber (sem campos internos)."""
    return {"id": m["id"], "kind": m["kind"], "label": m["label"], "provider": m["provider"],
            "caps": m["caps"], "cost": m["cost"], "cost_unit": m["cost_unit"], "is_default": m["is_default"]}


def for_user(user: dict, kind: str | None = None) -> list[dict]:
    allowed = user["limits"].get("models")
    return [m for m in list_models(kind) if m["available"] and (allowed is None or m["id"] in allowed)]


def pick(user: dict, kind: str, model_id=None, need: dict | None = None) -> dict | None:
    """Modelo escolhido no nó, ou o melhor automático: padrão do admin que atende a necessidade, senão o mais barato."""
    need = need or {}
    ok = lambda m: all(m["caps"].get(k) for k, v in need.items() if v)  # noqa: E731
    pool = for_user(user, kind)
    if model_id not in (None, "", "auto"):
        m = next((m for m in pool if str(m["id"]) == str(model_id)), None)
        if m and ok(m):
            return m
        if m:
            return None  # escolhido não serve para esta ligação (ex.: só texto→vídeo com imagem conectada)
    cands = [m for m in pool if ok(m)]
    cands.sort(key=lambda m: (not m["is_default"], m["cost"]))
    return cands[0] if cands else None


def estimate(m: dict, seconds: float = 5) -> float:
    return round(m["cost"] * (seconds if m["cost_unit"] == "second" else 1), 4)



IMAGE_TOKENS = 1290  # tokens de saída típicos de uma imagem ~1MP (Gemini/OpenAI image)


# Tokens de vídeo por segundo em 720p/24fps (Seedance cobra por token): 1280*720*24/1024.
VIDEO_TOKENS_PER_SEC = 21600


def or_video_price(skus: dict) -> tuple[float, bool] | None:
    """US$/segundo de um modelo de vídeo do OpenRouter (prefere sem áudio, 720p). (preço, é_estimativa)."""
    num = lambda k: float(skus[k]) if skus.get(k) not in (None, "") else None  # noqa: E731
    for k in ("duration_seconds_without_audio_720p", "duration_seconds_without_audio", "duration_seconds_720p",
              "image_to_video_duration_seconds_720p", "text_to_video_duration_seconds_720p", "duration_seconds",
              "duration_seconds_with_audio_720p", "duration_seconds_480p"):
        if num(k):
            return num(k), False
    for k in ("cents_per_video_output_second_720p", "cents_per_second_output_720p", "cents_per_second_output",
              "cents_per_video_output_second_480p"):
        if num(k):
            return num(k) / 100, False
    for k in ("video_tokens_without_audio", "video_tokens"):
        if num(k):
            return num(k) * VIDEO_TOKENS_PER_SEC, True
    for k, v in skus.items():  # qualquer outro preço por segundo
        if "second" in k and v:
            return float(v) / (100 if "cents" in k else 1), True
    return None


def introspect_openrouter_video(model_id: str) -> dict:
    m = next((x for x in providers.openrouter_video_catalog() if x["id"] == model_id), None)
    if not m:
        raise providers.ProviderError("modelo não encontrado no catálogo de vídeo do OpenRouter")
    frames = m.get("supported_frame_images") or []
    price = or_video_price(m.get("pricing_skus") or {})
    caps = {"prompt": True, "t2v": True, "i2v": "first_frame" in frames, "end_frame": "last_frame" in frames, "refs": False,
            "durations": [float(d) for d in (m.get("supported_durations") or [])], "audio": bool(m.get("generate_audio"))}
    fields = {"api": "openrouter_video", "resolutions": m.get("supported_resolutions") or [],
              "aspects": m.get("supported_aspect_ratios") or [], "seed": bool(m.get("seed"))}
    return {"kind": "video", "caps": caps, "fields": fields, "name": m.get("name"),
            "cost": round(price[0], 4) if price else 0.0, "cost_unit": "second"}


def introspect_openrouter(model_id: str, kind: str) -> dict:
    if kind == "video":
        return introspect_openrouter_video(model_id)
    cat = {m["id"]: m for m in providers.openrouter_catalog()}
    m = cat.get(model_id)
    if not m:
        raise providers.ProviderError("modelo não encontrado no catálogo do OpenRouter")
    if kind == "image" and "image" not in m["output"]:
        raise providers.ProviderError("este modelo do OpenRouter não gera imagem")
    if kind == "vision" and "image" not in m["input"]:
        raise providers.ProviderError("este modelo do OpenRouter não lê imagens")
    caps = {"t2i": True, "refs": "image" in m["input"]} if kind == "image" else {"vision": "image" in m["input"]}
    pr = m.get("pricing") or {}
    cost = float(pr.get("image_output") or 0) * IMAGE_TOKENS if kind == "image" else 0.0  # texto: custo real vem na resposta
    return {"kind": kind, "caps": caps, "fields": {}, "name": m["name"], "cost": round(cost, 4), "cost_unit": "run"}


def add_model(provider: str, model_id: str, kind: str | None = None, label: str | None = None,
              cost: float | None = None, cost_unit: str | None = None, is_default: bool = False,
              params: dict | None = None) -> dict:
    model_id = model_id.strip().strip("/")
    if provider == "openrouter":
        if kind not in ("text", "vision", "image", "video"):
            raise providers.ProviderError("no OpenRouter escolha: texto, visão, imagem ou vídeo")
        info = introspect_openrouter(model_id, kind)
        label = label or info.get("name")
        if cost is None and info.get("cost"):
            cost, cost_unit = info["cost"], info.get("cost_unit", "run")
    else:
        raise providers.ProviderError("provedor desconhecido")
    mid = db.run("INSERT INTO models(kind, provider, model_id, label, caps, fields, params, cost, cost_unit, created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider, model_id, kind) DO UPDATE SET "
                 "caps=excluded.caps, fields=excluded.fields",
                 (kind, provider, model_id, label or model_id.split("/")[-1], json.dumps(info["caps"]),
                  json.dumps(info["fields"]), json.dumps(params or {}), float(cost or 0), cost_unit or "run", db.now()))
    m = db.one("SELECT id FROM models WHERE provider = ? AND model_id = ? AND kind = ?", (provider, model_id, kind))
    if is_default:
        set_default(m["id"])
    return get(m["id"])


def refresh(mid: int) -> dict:
    """Relê capacidades/campos e, se o custo estiver zerado, o preço do provedor."""
    m = get(mid)
    info = introspect_openrouter(m["model_id"], m["kind"])
    db.run("UPDATE models SET caps = ?, fields = ? WHERE id = ?", (json.dumps(info["caps"]), json.dumps(info["fields"]), mid))
    if not m["cost"]:
        price = None
        if info.get("cost"):
            price = (info["cost"], info.get("cost_unit", "run"))
        if price:
            db.run("UPDATE models SET cost = ?, cost_unit = ? WHERE id = ?", (price[0], price[1], mid))
    return get(mid)


def backfill_costs():
    """Modelos de imagem/vídeo cadastrados sem preço ganham o preço do provedor (roda ao iniciar)."""
    for m in list_models():
        if m["kind"] in ("image", "video") and not m["cost"]:
            try:
                refresh(m["id"])
            except Exception as e:  # noqa: BLE001 - sem internet/sem key: tenta de novo no próximo start
                print(f"[preço] {m['model_id']}: {e}")


def build_or_video_input(m: dict, *, prompt: str, start: str | None, end: str | None, refs: list[str],
                         aspect: str, duration: float, quality: str, seed: int | None) -> dict:
    f = m["fields"]
    durs = m["caps"].get("durations") or []
    body = {"model": m["model_id"], "prompt": prompt}
    if durs:
        body["duration"] = int(_closest(duration, durs))
    res = f.get("resolutions") or []
    if res:
        order = sorted(res, key=lambda r: int("".join(c for c in r if c.isdigit()) or 0))
        body["resolution"] = order[0] if quality == "draft" else next((r for r in ("1080p", "720p") if r in res), order[-1])
    aspects = f.get("aspects") or []
    if aspects:
        body["aspect_ratio"] = aspect if aspect in aspects else aspects[0]
    if not m["caps"].get("refs") or (end and m["caps"].get("end_frame")):
        refs = []  # modelo recusa (HTTP 400) ou ignora input_references; com frame final (transição) valem os frames
    if refs and not (end and m["caps"].get("end_frame")):
        # referência→vídeo: o 1º frame entra como imagem 1 (o modelo não mistura frame_images com referências)
        imgs = ([start] if start else []) + list(refs)
        body["input_references"] = [{"type": "image_url", "image_url": {"url": u}} for u in imgs[:m["caps"].get("max_refs") or 4]]
        refs, start = [], None
        fixed = OR_VIDEO_REFS_DURATIONS.get(m["model_id"])
        if fixed:
            body["duration"] = int(_closest(duration, fixed))
    frames = []
    if start and m["caps"].get("i2v"):
        frames.append({"type": "image_url", "image_url": {"url": start}, "frame_type": "first_frame"})
    if end and m["caps"].get("end_frame"):
        frames.append({"type": "image_url", "image_url": {"url": end}, "frame_type": "last_frame"})
    if frames:
        body["frame_images"] = frames
    if refs:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": u}} for u in refs]
    if m["caps"].get("audio"):
        body["generate_audio"] = False  # áudio vem da montagem; o admin liga em params
    if seed is not None and f.get("seed"):
        body["seed"] = seed
    body.update(m["params"] or {})
    return body


def role(m: dict) -> str:
    c = m["caps"]
    if m["kind"] == "video":
        return "i2v" if c.get("i2v") else "t2v"
    if m["kind"] == "image":
        return "refs" if c.get("refs") and not c.get("t2i") else "t2i"
    return m["kind"]


def set_default(mid: int):
    """Padrão por tipo e por capacidade principal (vídeo: um para texto→vídeo, outro para imagem→vídeo)."""
    m = get(mid)
    same = [x for x in list_models(m["kind"]) if x["id"] != mid and role(x) == role(m)]
    for x in same:
        db.run("UPDATE models SET is_default = 0 WHERE id = ?", (x["id"],))
    db.run("UPDATE models SET is_default = 1 WHERE id = ?", (mid,))


def seed():
    """Primeiro start: cadastra modelos sugeridos (só aparecem no estúdio quando a key do provedor existir)."""
    if db.get_setting("models_seeded"):
        return
    for kind, prov, mid, label, default, cost, unit in SEED:
        try:
            m = add_model(prov, mid, kind, label, cost, unit)
            db.run("UPDATE models SET is_default = ?, cost = ?, cost_unit = ? WHERE id = ?", (default, cost, unit, m["id"]))
        except Exception as e:  # noqa: BLE001 - sem internet no build/start: cadastra sem schema
            print(f"[seed] {mid}: {e}")
            db.run("INSERT OR IGNORE INTO models(kind, provider, model_id, label, is_default, cost, cost_unit, caps, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (kind, prov, mid, label, default, cost, unit,
                                                json.dumps({"t2v": "text-to" in mid, "i2v": "image-to" in mid,
                                                            "t2i": "edit" not in mid, "refs": prov == "openrouter" or "edit" in mid}),
                                                db.now()))
    db.set_setting("models_seeded", True)


# ---------- montagem dos parâmetros de entrada ----------



def _closest(value: float, options: list) -> str | int | float:
    num = [(abs(float(o) - value), o) for o in options if str(o).replace(".", "").isdigit()]
    return min(num)[1] if num else options[0]


