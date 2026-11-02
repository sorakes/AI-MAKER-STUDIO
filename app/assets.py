"""Estúdio de personagens e produtos.

Cada item (asset) tem ficha (nome, descrição visual, atributos), referências enviadas pelo usuário
(cada uma com nota: "camisa", "logo no peito"...) e as imagens geradas. As imagens marcadas como
"usar" viram as referências do nó Elenco/Produto no canvas.
"""
import json
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import db, prompts, storage

KINDS = {"character": "character_sheet", "product": "product_sheet"}
VIEWS = {
    "character": {
        "sheet": "character turnaround sheet: front view, three-quarter view and side view of the same person side by side",
        "portrait": "close-up portrait, head and shoulders, looking at camera",
        "full": "full body shot, standing, relaxed natural pose",
        "action": "dynamic natural action pose, full body",
    },
    "product": {
        "sheet": "product sheet: front, three-quarter and side views of the same product side by side",
        "hero": "hero shot, three-quarter angle, slightly low camera",
        "front": "straight front view, centered",
        "detail": "macro detail of the label, logo and materials",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_user ON assets(user_id, kind);
"""


class Ref(BaseModel):
    url: str
    note: str = ""  # o que é: "camisa", "logo para o peito", "rosto"...


class AssetBody(BaseModel):
    kind: str = "character"
    name: str = "Sem nome"
    description: str = ""
    attributes: dict = Field(default_factory=dict)  # campos livres da ficha
    refs: list[Ref] = Field(default_factory=list)
    images: list[dict] | None = None  # [{url, use: bool, view, job}] — só reordenar/marcar


class GenerateBody(BaseModel):
    view: str = "sheet"
    extra: str = ""
    model: str | int | None = "auto"
    aspect: str = "16:9"
    count: int = 1


def init():
    with db._lock:
        db._conn.executescript(SCHEMA)


def _row(r: dict) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "created": r["created"], "updated": r["updated"],
            "description": d.get("description", ""), "attributes": d.get("attributes", {}),
            "refs": d.get("refs", []), "images": d.get("images", []), "pending": d.get("pending", []),
            "saved": bool(d.get("saved")), "saved_at": d.get("saved_at"), "cover": d.get("cover")}


def list_for(user: dict, kind: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM assets WHERE user_id = ?", [user["id"]]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    return [_row(r) for r in db.all_(sql + " ORDER BY updated DESC", args)]


def _visible(r: dict | None, user: dict) -> dict:
    if not r or (r["user_id"] != user["id"] and not user.get("is_admin", user.get("role") == "admin")):
        raise HTTPException(404, "Item não encontrado")
    return _row(r)


def get(asset_id: str, user: dict) -> dict:
    return _visible(db.one("SELECT * FROM assets WHERE id = ?", (asset_id,)), user)


async def aget(asset_id: str, user: dict) -> dict:
    return _visible(await db.aone("SELECT * FROM assets WHERE id = ?", (asset_id,)), user)


def _write(a: dict, user_id: int):
    data = {k: a.get(k) for k in ("description", "attributes", "refs", "images", "pending", "saved", "saved_at", "cover")}
    db.run("INSERT INTO assets(id, user_id, kind, name, data, created, updated) VALUES(?,?,?,?,?,?,?) "
           "ON CONFLICT(id) DO UPDATE SET name = excluded.name, data = excluded.data, updated = excluded.updated",
           (a["id"], user_id, a["kind"], a["name"], json.dumps(data, ensure_ascii=False), a.get("created") or time.time(), time.time()))


def create(user: dict, body: AssetBody) -> dict:
    if body.kind not in KINDS:
        raise HTTPException(400, "Tipo inválido")
    for r in body.refs:
        storage.resolve(r.url, user)
    a = {"id": f"{body.kind[:4]}-{uuid.uuid4().hex[:8]}", "kind": body.kind, "name": body.name.strip()[:80] or "Sem nome",
         "description": body.description, "attributes": body.attributes, "refs": [r.model_dump() for r in body.refs],
         "images": [], "pending": [], "created": time.time()}
    _write(a, user["id"])
    return get(a["id"], user)


def update(asset_id: str, user: dict, body: AssetBody) -> dict:
    a = get(asset_id, user)
    for r in body.refs:
        storage.resolve(r.url, user)
    a.update({"name": body.name.strip()[:80] or a["name"], "description": body.description, "attributes": body.attributes,
              "refs": [r.model_dump() for r in body.refs]})
    if body.images is not None:  # só marca/desmarca/remove: nunca aceita URL nova do cliente
        known = {i["url"]: i for i in a["images"]}
        a["images"] = [{**known[i["url"]], "use": bool(i.get("use"))} for i in body.images if i.get("url") in known]
    _write(a, user["id"])
    return get(asset_id, user)


class SaveBody(BaseModel):
    name: str | None = None
    images: list[dict] = Field(default_factory=list)  # [{url, view?, label?}] escolhidas no nó Salvar
    cover: str | None = None
    discard: list[str] = Field(default_factory=list)  # versões antigas geradas para o item: apagar ao salvar
    tab: str | None = None  # aba do canvas onde o Salvar foi clicado (ela é atualizada pelo navegador)


def save_final(asset_id: str, user: dict, body: SaveBody) -> dict:
    """Nó Salvar: fixa quais imagens valem (em uso) e publica o item na biblioteca."""
    a = get(asset_id, user)
    if not body.images:
        raise HTTPException(400, "Escolha pelo menos uma imagem para salvar")
    chosen = {}
    for im in body.images[:40]:
        storage.resolve(im["url"], user)
        chosen[im["url"]] = im
    known = {i["url"]: i for i in a["images"]}
    for url, im in chosen.items():
        if url not in known:
            a["images"].append({"url": url, "view": im.get("view"), "label": im.get("label"), "at": time.time()})
    for i in a["images"]:
        i["use"] = i["url"] in chosen
        if i["url"] in chosen and chosen[i["url"]].get("label"):
            i["label"] = chosen[i["url"]]["label"]
    if body.name and body.name.strip():
        a["name"] = body.name.strip()[:80]
    a["cover"] = body.cover if body.cover in chosen else next(iter(chosen))
    a["saved"], a["saved_at"] = True, time.time()
    deleted = _discard_versions(a, user, set(body.discard) - set(chosen), body.tab)
    a["images"] = [i for i in a["images"] if i["url"] not in deleted]
    _write(a, user["id"])
    return {**get(asset_id, user), "deleted": sorted(deleted)}


def _discard_versions(a: dict, user: dict, urls: set[str], tab: str | None = None) -> set[str]:
    """Apaga versões antigas geradas para o item (só renders, nunca uploads/referências),
    desde que nada mais use o arquivo: outro item, as referências deste ou um canvas de outra área."""
    if not urls:
        return set()
    others = db.all_("SELECT id, data FROM assets WHERE user_id = ?", (user["id"],))
    used = {r["url"] for r in a.get("refs", [])}
    for r in others:
        if r["id"] == a["id"]:
            continue
        d = json.loads(r["data"] or "{}")
        used |= {i["url"] for i in d.get("images", [])} | {x["url"] for x in d.get("refs", [])}
    here = tab if tab and tab.split("~")[0] == a["kind"] else a["kind"]
    docs = " ".join(r["doc"] for r in db.all_("SELECT doc FROM canvases WHERE user_id = ? AND ws != ?", (user["id"], here)))
    deleted = set()
    for url in list(urls)[:300]:
        try:
            path = storage.resolve(url, user)
        except HTTPException:
            continue
        if path.parent.name != "renders" or url in used or url in docs:
            continue
        storage.delete(url, user)
        side = path.with_suffix(".json")  # mapa da spritesheet
        if path.name.startswith("spritesheet_") and side.exists():
            side.unlink()
        deleted.add(url)
    if deleted:
        from . import usage
        usage.log("delete", detail={"files": len(deleted), "reason": f"versões antigas de {a['name']}"})
    return deleted


def files_index(user: dict) -> dict[str, dict]:
    """url -> item (personagem/produto) a que o arquivo pertence: o inventário mostra dentro do item."""
    out = {}
    for a in list_for(user):
        for i in a["images"]:
            out[i["url"]] = {"id": a["id"], "name": a["name"], "kind": a["kind"], "saved": a["saved"], "use": bool(i.get("use"))}
    return out


def delete(asset_id: str, user: dict):
    get(asset_id, user)
    db.run("DELETE FROM assets WHERE id = ?", (asset_id,))


def build_prompt(a: dict, view: str, extra: str, refs: list[dict] | None = None) -> str:
    attrs = "; ".join(f"{k}: {v}" for k, v in (a.get("attributes") or {}).items() if str(v).strip())
    refs = refs if refs is not None else list(a.get("refs", []))
    ref_txt = " ".join(f"Image {i}: {r['note'] or 'reference'}." for i, r in enumerate(refs, 1))
    if ref_txt:
        ref_txt = f"Reference images (use them exactly): {ref_txt}"
    return prompts.fill(KINDS[a["kind"]], name=a["name"], description=a.get("description") or "",
                        attributes=attrs, references=ref_txt, view=VIEWS[a["kind"]].get(view, view), extra=extra)


def generate(asset_id: str, user: dict, body: GenerateBody) -> dict:
    from . import jobs  # tardio: jobs importa este módulo no gancho
    a = get(asset_id, user)
    prompt = build_prompt(a, body.view, body.extra)
    out = []
    for _ in range(max(1, min(4, body.count))):
        job = jobs.submit(user, jobs.RenderRequest(
            node_id=f"asset:{a['id']}", name=f"{a['name']} · {body.view}", gen="image", model=body.model,
            prompt=prompt, keyframe_prompt=prompt, aspect=body.aspect,
            ref_image_urls=[r["url"] for r in a["refs"]], adapt=False))
        out.append(job["id"])
    a["pending"] = [*a.get("pending", []), *[{"job": j, "view": body.view} for j in out]]
    _write(a, user["id"])
    return {"jobs": out, "prompt": prompt}


def on_job_done(job: dict):
    """Gancho do jobs.py: render de um asset terminou -> entra na galeria dele."""
    node = (job.get("spec") or {}).get("node_id") or ""
    if not node.startswith("asset:"):
        return
    asset_id, _, view = node[6:].partition(":")
    r = db.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not r:
        return
    a = _row(r)
    pend = next((p for p in a["pending"] if p["job"] == job["id"]), {"view": view or None})
    a["pending"] = [p for p in a["pending"] if p["job"] != job["id"]]
    if job["status"] == "done" and job.get("output"):
        v = pend.get("view")
        if v:
            for i in a["images"]:
                if i.get("view") == v:
                    i["use"] = False
        a["images"].append({"url": job["output"], "use": bool(v) or not any(i.get("use") for i in a["images"]),
                            "view": pend.get("view"), "job": job["id"], "at": time.time()})
    _write(a, r["user_id"])
