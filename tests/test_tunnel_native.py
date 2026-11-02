"""Túnel público + edição de vídeo nativa: links temporários, portão do túnel e vários usuários ao mesmo tempo.
Nada aqui chama o provedor: o modelo de edição é simulado (custo zero)."""
import shutil
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import media, storage, tunnel, videoedit
from app.main import app

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe não estão no PATH")
HOST = "teste-aleatorio.trycloudflare.com"


@pytest.fixture(autouse=True)
def fake_tunnel(monkeypatch):
    monkeypatch.setattr(tunnel, "public_host", lambda: HOST)
    tunnel._links.clear()
    yield
    tunnel._links.clear()


def _clip(path, seconds):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=320x180:rate=25", "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True, capture_output=True)
    return path


def _file(tmp_path, name="a.bin", data=b"conteudo"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---------- links ----------

def test_link_serves_only_its_file_and_dies_on_revoke(tmp_path):
    p = _file(tmp_path)
    token, url = tunnel.create(p)
    assert url == f"https://{HOST}/pub/{token}/a.bin"
    assert tunnel.lookup(token, "a.bin") == p
    assert tunnel.lookup(token, "outro.bin") is None  # nome errado
    assert tunnel.lookup("token-inventado", "a.bin") is None
    tunnel.revoke(token)
    assert tunnel.lookup(token, "a.bin") is None and tunnel.open_links() == 0


def test_link_expires_by_ttl(tmp_path):
    token, _ = tunnel.create(_file(tmp_path), ttl=0.2)
    assert tunnel.lookup(token, "a.bin")
    time.sleep(0.3)
    assert tunnel.lookup(token, "a.bin") is None and tunnel.open_links() == 0


def test_ttl_cannot_exceed_the_cap(tmp_path):
    token, _ = tunnel.create(_file(tmp_path), ttl=10**9)
    assert tunnel._links[token]["exp"] - time.time() <= tunnel.TTL_S + 1


def test_shared_revokes_even_when_the_work_fails(tmp_path):
    p = _file(tmp_path)
    with pytest.raises(RuntimeError):
        with tunnel.shared(p):
            assert tunnel.open_links() == 1
            raise RuntimeError("provedor caiu")
    assert tunnel.open_links() == 0


def test_without_tunnel_it_is_unavailable_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(tunnel, "public_host", lambda: "")
    assert not tunnel.available() and not videoedit.native_available()
    with pytest.raises(tunnel.TunnelError):
        tunnel.create(_file(tmp_path))


def test_tokens_are_unique_and_long(tmp_path):
    p = _file(tmp_path)
    toks = {tunnel.create(p)[0] for _ in range(200)}
    assert len(toks) == 200 and all(len(t) >= 32 for t in toks)


# ---------- portão: pelo túnel só /pub responde ----------

def test_through_the_tunnel_only_pub_answers(tmp_path):
    p = _file(tmp_path, data=b"video-bytes")
    token, _ = tunnel.create(p)
    c = TestClient(app, base_url=f"http://{HOST}")
    ok = c.get(f"/pub/{token}/a.bin")
    assert ok.status_code == 200 and ok.content == b"video-bytes" and ok.headers["cache-control"] == "no-store"
    for path in ("/", "/login", "/admin", "/health", "/api/me", "/files/1/renders/x.mp4", "/js/app.js"):
        assert c.get(path).status_code == 404, path
    assert c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 404
    assert c.get("/pub/token-errado/a.bin").status_code == 404


def test_cloudflare_header_also_counts_as_tunnel(tmp_path):
    c = TestClient(app, base_url="http://localhost")
    assert c.get("/health", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 404
    assert c.get("/health").status_code == 200  # local continua normal


def test_local_access_to_pub_works_after_revoke_it_does_not(tmp_path):
    token, _ = tunnel.create(_file(tmp_path))
    c = TestClient(app)
    assert c.get(f"/pub/{token}/a.bin").status_code == 200
    tunnel.revoke(token)
    assert c.get(f"/pub/{token}/a.bin").status_code == 404


# ---------- trechos ----------

def test_chunks_respect_the_model_limit_and_cover_the_range():
    for a, b in ((0, 3), (0, 14), (0, 30), (5, 95), (0, 120)):
        ch = videoedit.native_chunks(a, b)
        assert ch[0][0] == a and ch[-1][1] == b
        assert all(y - x <= 15 for x, y in ch)
        assert all(abs(ch[i][1] - ch[i + 1][0]) < 1e-6 for i in range(len(ch) - 1))
    assert len(videoedit.native_chunks(0, 30)) == 3


def test_native_allows_long_videos_frames_do_not():
    videoedit.validate_segment(0, 120, 200, native=True)
    with pytest.raises(ValueError, match="no máximo 120"):
        videoedit.validate_segment(0, 121, 200, native=True)
    with pytest.raises(ValueError, match="no máximo 10"):
        videoedit.validate_segment(0, 30, 200)


def test_native_estimate_is_per_second():
    assert videoedit.estimate_native(30) == {"chunks": 3, "usd": 0.96, "native": True}  # 30 s + 2 emendas de 1 s


# ---------- edição nativa real (ffmpeg) com o modelo simulado ----------

def _fetch(url, client):
    """O 'provedor': baixa o pedaço pelo link público, como o servidor da OpenRouter faria."""
    r = client.get(url.split(HOST, 1)[1])
    assert r.status_code == 200, r.status_code
    return r.content


@needs_ffmpeg
def test_run_native_edits_the_whole_video_in_chunks_and_closes_every_link(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 30)
    dest = storage.user_dir(1, "renders") / f"nat-{time.time_ns()}.mp4"
    seen = []
    c = TestClient(app, base_url=f"http://{HOST}")

    def fake(prompt, url, ref=None):
        assert "Edit this video" in prompt and url.startswith(f"https://{HOST}/pub/")
        seen.append(url)
        return _fetch(url, c), 0.3

    cost = videoedit.run_native(src, {"start": 0, "end": 30, "prompt": "troque a roupa", "preset": "none",
                                      "strength": "high", "preserve_audio": True, "user_id": 1}, dest, fake)
    assert len(seen) == 3 and abs(cost - 0.9) < 1e-9
    assert abs(media.probe(dest)["duration"] - 30) < 0.5
    assert media.has_audio(dest)
    assert tunnel.open_links() == 0  # nenhum link sobrou
    for u in seen:  # e os links já não respondem
        assert c.get(u.split(HOST, 1)[1]).status_code == 404


@needs_ffmpeg
def test_failure_in_one_chunk_still_closes_all_links(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 30)
    dest = storage.user_dir(1, "renders") / f"fail-{time.time_ns()}.mp4"
    n = {"i": 0}
    lock = threading.Lock()

    def fake(prompt, url, ref=None):
        with lock:
            n["i"] += 1
            k = n["i"]
        if k == 2:
            raise RuntimeError("provedor recusou")
        time.sleep(0.2)
        return b"x", 0.0

    with pytest.raises(Exception):
        videoedit.run_native(src, {"start": 0, "end": 30, "prompt": "", "preset": "night", "strength": "low",
                                   "preserve_audio": False, "user_id": 1}, dest, fake)
    assert tunnel.open_links() == 0


@needs_ffmpeg
def test_six_users_edit_at_the_same_time_without_mixing_files(tmp_path):
    users = 6
    srcs = []
    for u in range(users):  # cada usuário com um vídeo de duração diferente (5..10 s) para provar que não se misturam
        srcs.append(_clip(tmp_path / f"u{u}.mp4", 5 + u))
    results, errors, peak = {}, [], {"v": 0}
    c = TestClient(app, base_url=f"http://{HOST}")
    barrier = threading.Barrier(users)

    def worker(u):
        try:
            dest = storage.user_dir(100 + u, "renders") / f"sim-{time.time_ns()}.mp4"
            expect = srcs[u]

            def fake(prompt, url, ref=None):
                peak["v"] = max(peak["v"], tunnel.open_links())
                data = _fetch(url, c)
                got = tmp_path / f"got-{u}.mp4"
                got.write_bytes(data)
                # o pedaço baixado é do vídeo DESTE usuário: mesma duração da fonte
                assert abs(media.probe(got)["duration"] - media.probe(expect)["duration"]) < 0.5
                time.sleep(0.3)
                return data, 0.1

            barrier.wait(timeout=30)  # todos começam juntos
            videoedit.run_native(expect, {"start": 0, "end": media.probe(expect)["duration"], "prompt": f"u{u}",
                                          "preset": "anime", "strength": "medium", "preserve_audio": True,
                                          "user_id": 100 + u}, dest, fake)
            results[u] = media.probe(dest)["duration"]
        except BaseException as e:  # noqa: BLE001
            errors.append((u, repr(e)))

    ts = [threading.Thread(target=worker, args=(u,)) for u in range(users)]
    [t.start() for t in ts]
    [t.join(timeout=240) for t in ts]
    assert not errors, errors
    assert len(results) == users
    for u in range(users):
        assert abs(results[u] - (5 + u)) < 0.6, (u, results[u])
    assert peak["v"] >= 2  # houve de fato links abertos ao mesmo tempo
    assert tunnel.open_links() == 0


# ---------- escolha de modelo, vídeo inteiro e info do node ----------

from app import auth, jobs, registry, usage  # noqa: E402


@pytest.fixture
def no_ai(monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)


def _user_client(login, make_user):
    u = make_user(f"nat-{time.time_ns()}@test.local")
    c = login(u["email"], c=TestClient(app))
    return c, auth.public_user(u)


def test_choose_model_auto_follows_the_tunnel(monkeypatch, make_user):
    user = auth.public_user(make_user(f"cm-{time.time_ns()}@test.local"))
    assert videoedit.choose_model(user, "auto")["model_id"] == videoedit.NATIVE_MODEL
    assert videoedit.choose_model(user, "flux")["usd_per_s"] == videoedit.NATIVE_USD_PER_S
    with pytest.raises(ValueError, match="recebem o vídeo"):
        videoedit.choose_model(user, "frames")  # o caminho por quadros saiu deste node
    with pytest.raises(ValueError, match="desconhecido"):
        videoedit.choose_model(user, "nao-existe")
    monkeypatch.setattr(tunnel, "public_host", lambda: "")
    for sel in ("auto", "flux"):  # sem túnel o vídeo não chega ao modelo: erro claro, nunca um caminho alternativo
        with pytest.raises(ValueError, match="túnel"):
            videoedit.choose_model(user, sel)


@needs_ffmpeg
def test_info_quote_and_whole_video_through_the_api(login, make_user, no_ai, monkeypatch):
    c, me = _user_client(login, make_user)
    url = storage.to_url(_clip(storage.user_dir(me["id"], "uploads") / f"w-{time.time_ns()}.mp4", 30))
    info = c.get("/api/video/edit/info", params={"source_url": url}).json()
    assert info["tunnel"] is True and 29 < info["duration"] < 31 and info["models"][0]["id"] == "flux"
    assert info["native_max"] == 120
    assert "tokens_per_s" in info["models"][0] and "usd_per_s" not in info["models"][0]  # usuário vê tokens, não dinheiro
    q = c.post("/api/video/edit/quote", json={"source_url": url, "whole": True, "preset": "anime"})
    assert q.status_code == 200, q.text
    j = q.json()
    assert j["native"] is True and j["chunks"] == 3 and 0.8 < j["usd"] < 1.0 and j["confirm"] is True  # 30 s + emendas ≈ US$ 0,96
    r = c.post("/api/video/edit/quote", json={"source_url": url, "whole": True, "model": "frames"})
    assert r.status_code == 400 and "recebem o vídeo" in r.json()["detail"]  # por quadros não existe mais aqui
    # o job guarda o modelo escolhido e o preço para o estimado do orçamento
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    fake = {"id": "img-fake", "kind": "image", "label": "x", "provider": "openrouter", "model_id": "x/y",
            "caps": {"t2i": True, "refs": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.034,
            "cost_unit": "run", "key_id": None, "key_name": "", "is_default": 1, "enabled": 1}
    monkeypatch.setattr(registry, "pick", lambda *a, **k: fake)
    monkeypatch.setattr(registry, "get", lambda mid, *a, **k: fake if mid == fake["id"] else None)
    r = c.post("/api/video/edit", json={"source_url": url, "whole": True, "preset": "night", "model": "flux"})
    assert r.status_code == 200, r.text
    op = started[0]["spec"]["local_op"]
    assert op["native"] is True and op["model_id"] == videoedit.NATIVE_MODEL and op["end"] > 29
    st = started[0]["steps"][0]  # a etapa mostra quem edita de verdade (antes: o modelo de imagem, ex. Nano Banana)
    assert st["model_label"] == "FLUX Video Edit" and st["label"] == "Edição do vídeo" and st["model"] == "vedit:flux"
    assert 0.8 < started[0]["est_cost"] < 1.0


def test_info_without_tunnel_says_so(login, make_user, monkeypatch):
    monkeypatch.setattr(tunnel, "public_host", lambda: "")
    c, me = _user_client(login, make_user)
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg ausente")
    url = storage.to_url(_clip(storage.user_dir(me["id"], "uploads") / f"n-{time.time_ns()}.mp4", 4))
    info = c.get("/api/video/edit/info", params={"source_url": url}).json()
    assert info["tunnel"] is False and info["models"] == []


def test_node_shows_the_linked_video_and_has_model_and_whole_video_options():
    from pathlib import Path
    js = Path(__file__).resolve().parents[1] / "static" / "js"
    ve = (js / "videoedit.js").read_text(encoding="utf-8")
    cv = (js / "canvas.js").read_text(encoding="utf-8")
    assert "source: src" in ve and "source ? `<video src=" in cv  # o vídeo ligado aparece no node
    assert 'data-k="model"' in ve and "opt('frames'" not in ve  # seletor só com modelos que recebem vídeo
    assert 'data-k="whole"' in ve and "whole: true" in ve  # vídeo inteiro por padrão
    assert "/api/video/edit/info" in ve


def test_user_facing_cost_is_tokens_not_dollars_in_the_video_edit_node():
    from pathlib import Path
    js = Path(__file__).resolve().parents[1] / "static" / "js"
    for name in ("videoedit.js", "performance.js"):
        src = (js / name).read_text(encoding="utf-8")
        assert "US$" not in src, name  # o estúdio só mostra tokens
        assert "fmtTok(" in src


# ---------- corrente: cada pedaço continua do último quadro editado do anterior ----------

def _color_clip(path, color, seconds):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={color}:size=320x180:rate=25:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True, capture_output=True)
    return path.read_bytes()


def _mean_rgb(png_bytes):
    import io
    from PIL import Image
    px = list(Image.open(io.BytesIO(png_bytes)).convert("RGB").getdata())
    return tuple(sum(p[i] for p in px) // len(px) for i in range(3))


@needs_ffmpeg
def test_native_chain_runs_in_sequence_and_passes_the_last_edited_frame(tmp_path):
    import base64
    src = _clip(tmp_path / "fonte.mp4", 30)
    dest = storage.user_dir(1, "renders") / f"chain-{time.time_ns()}.mp4"
    colors = ["red", "green", "blue"]  # o "modelo" devolve um pedaço de cada cor: dá para ver de onde veio a referência
    calls, active = [], {"n": 0, "max": 0}

    def fake(prompt, url, ref=None):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        k = len(calls)
        calls.append({"prompt": prompt, "ref": ref})
        out = _color_clip(tmp_path / f"o{k}.mp4", colors[k], 10)
        active["n"] -= 1
        return out, 0.1

    videoedit.run_native(src, {"start": 0, "end": 30, "prompt": "troque a roupa", "preset": "none", "strength": "high",
                               "preserve_audio": False, "user_id": 1}, dest, fake)
    assert len(calls) == 3 and active["max"] == 1  # nunca dois pedaços do mesmo vídeo ao mesmo tempo
    assert calls[0]["ref"] is None and "LAST FRAME" not in calls[0]["prompt"]  # o primeiro não tem anterior
    for k, want in ((1, (255, 0, 0)), (2, (0, 128, 0))):  # referência do pedaço k = último quadro EDITADO do k-1
        assert calls[k]["ref"].startswith("data:image/") and calls[k]["prompt"] == calls[0]["prompt"] + videoedit.PREFIX_NOTE
        got = _mean_rgb(base64.b64decode(calls[k]["ref"].split(",", 1)[1]))
        assert all(abs(g - w) < 60 for g, w in zip(got, want)), (k, got, want)
    assert tunnel.open_links() == 0


@needs_ffmpeg
def test_frames_chain_passes_the_previous_edited_frame(tmp_path):
    import base64
    import io
    from PIL import Image
    src = _clip(tmp_path / "fonte.mp4", 4)
    dest = storage.user_dir(1, "renders") / f"fchain-{time.time_ns()}.mp4"
    seen = []

    def uri(rgb):
        buf = io.BytesIO()
        Image.new("RGB", (320, 180), rgb).save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def fake(prompt, frame_url, prev=None):
        seen.append((prev, "PREVIOUS frame" in prompt))
        return uri((200, 20 * len(seen), 20)), 0.01

    videoedit.run(src, {"start": 0, "end": 4, "prompt": "", "preset": "anime", "strength": "medium",
                        "preserve_audio": False, "user_id": 1}, dest, fake)
    assert len(seen) == 4
    assert seen[0] == (None, False)
    assert all(prev and flag for prev, flag in seen[1:])  # do 2º quadro em diante: o anterior editado vai de referência


# ---------- escolha livre de modelo ----------

def test_model_list_has_dedicated_editors_and_every_registered_video_model(make_user):
    user = auth.public_user(make_user(f"ml-{time.time_ns()}@test.local"))
    fake = {"id": 77, "model_id": "bytedance/seedance-2.5", "label": "Seedance 2.5", "cost": 0.2311, "cost_unit": "second",
            "key_id": None, "caps": {}, "fields": {}, "params": {}}
    text_only = {**fake, "id": 78, "model_id": "google/veo-3.1", "label": "Veo 3.1"}  # só texto/imagem → vídeo
    orig = registry.for_user
    registry.for_user = lambda u, k: [fake, text_only] if k == "video" else []
    try:
        ms = videoedit.native_models(user)
    finally:
        registry.for_user = orig
    ids = [m["id"] for m in ms]
    assert ids[:2] == ["flux", "aleph"] and "m77" in ids  # o FLUX deixa de ser o único
    assert "m78" not in ids  # modelo que não recebe vídeo não entra no node
    seed = next(m for m in ms if m["id"] == "m77")
    assert seed["generic"] is True and "experimental" in seed["label"] and seed["reg_id"] == 77
    assert not ms[0]["generic"] and not ms[1]["generic"]


def test_runway_minimum_per_generation_and_per_model_chunk_size():
    assert videoedit.estimate_native(1, 0.28, 10.0, 0.56)["usd"] == 0.56  # 1 s: paga o mínimo
    assert videoedit.estimate_native(20, 0.28, 10.0, 0.56) == {"chunks": 3, "usd": 6.16, "native": True}  # 3 × 6,67 s + 2 emendas
    assert len(videoedit.native_chunks(0, 20, 10.0)) == 2 and len(videoedit.native_chunks(0, 20)) == 2
    assert len(videoedit.native_chunks(0, 25, 10.0)) == 3 and len(videoedit.native_chunks(0, 25, 14.0)) == 2


def _stub_provider(monkeypatch, bodies, reject_refs=False):
    from app import providers

    def submit(body, key_id=None):
        bodies.append((body, key_id))
        if reject_refs and any(r.get("type") == "image_url" for r in body["input_references"]):
            raise providers.ProviderError("HTTP 400: input_references image não aceita")
        return {"id": "v1", "polling_url": "/x", "key_id": key_id}

    monkeypatch.setattr(providers, "or_video_submit", submit)
    monkeypatch.setattr(providers, "or_video_status", lambda h: {"status": "completed", "usage": {"cost": 0.42}})
    monkeypatch.setattr(providers, "or_video_download", lambda h, s: b"mp4")
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)


def test_dedicated_model_body_is_just_prompt_and_video(monkeypatch):
    bodies = []
    _stub_provider(monkeypatch, bodies)
    data, cost = jobs._openrouter_video_edit({"key_id": 5}, "troque", "https://h/pub/t/a.mp4",
                                             {"model_id": "runway/aleph-2", "key_id": 9})
    body, key = bodies[0]
    assert body == {"model": "runway/aleph-2", "prompt": "troque",
                    "input_references": [{"type": "video_url", "video_url": {"url": "https://h/pub/t/a.mp4"}}]}
    assert key == 9 and data == b"mp4" and cost == 0.42  # custo REAL do provedor


def test_generic_model_gets_duration_aspect_and_the_video(monkeypatch):
    bodies = []
    _stub_provider(monkeypatch, bodies)
    reg = {"id": 77, "model_id": "bytedance/seedance-2.5", "caps": {"durations": [4, 5, 6, 8, 10], "refs": False},
           "fields": {"aspects": ["16:9", "9:16"], "resolutions": ["480p", "720p"]}, "params": {}}
    monkeypatch.setattr(registry, "get", lambda i, *a, **k: reg if i == 77 else None)
    op = {"generic": True, "reg_id": 77, "model_id": reg["model_id"], "key_id": None, "_aspect": "9:16", "_chunk_len": 9.6}
    jobs._openrouter_video_edit({"key_id": None}, "mude a roupa", "https://h/pub/t/a.mp4", op)
    body = bodies[0][0]
    assert body["model"] == "bytedance/seedance-2.5" and body["aspect_ratio"] == "9:16" and body["duration"] == 10
    assert body["input_references"] == [{"type": "video_url", "video_url": {"url": "https://h/pub/t/a.mp4"}}]


def test_continuity_image_is_appended_and_dropped_if_the_model_refuses_it(monkeypatch):
    bodies, job = [], {}
    _stub_provider(monkeypatch, bodies, reject_refs=True)
    op = {"model_id": "black-forest-labs/flux-video-edit", "key_id": None}
    jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", op, "data:image/png;base64,AAAA", job)
    assert len(bodies) == 2  # 1ª tentativa com a imagem (recusada), 2ª sem ela
    assert [r["type"] for r in bodies[0][0]["input_references"]] == ["video_url", "image_url"]
    assert [r["type"] for r in bodies[1][0]["input_references"]] == ["video_url"]
    assert "sem ela" in job["detail"]
    bodies.clear()
    _stub_provider(monkeypatch, bodies)  # aceita: fica só a primeira tentativa, com a imagem
    jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", op, "data:image/png;base64,AAAA", {})
    assert len(bodies) == 1 and bodies[0][0]["input_references"][1]["image_url"]["url"].startswith("data:image/png")


# ---------- referências (personagem/roupa) e os dois modos ----------

def test_swap_mode_changes_only_the_request_and_names_the_reference_images():
    op = {"mode": "swap", "prompt": "troque a roupa", "preset": "none", "strength": "high", "ref_urls": ["/files/1/a.png", "/files/1/b.png"]}
    t = videoedit.native_instruction(op)
    assert "Edit this video: troque a roupa" in t and "keep everything else identical" in t
    assert "Reference images 1-2 show the new character, outfit or object" in t and "strongly" in t
    one = videoedit.native_instruction({**op, "ref_urls": ["/files/1/a.png"]})
    assert "Reference image 1 shows the new" in one
    none = videoedit.native_instruction({**op, "ref_urls": []})
    assert "Reference image" not in none


def test_motion_mode_keeps_only_motion_and_rebuilds_from_the_references():
    op = {"mode": "motion", "prompt": "", "preset": "none", "strength": "medium", "ref_urls": ["/files/1/a.png"]}
    t = videoedit.native_instruction(op)
    assert t.startswith("Keep ONLY the motion, timing and camera work of this video") and "as shown in the references" in t
    assert "Edit this video" not in t
    assert "anime" in videoedit.native_instruction({**op, "mode": "swap", "preset": "anime"})


def test_reference_photos_travel_with_the_video_before_the_continuity_frame(monkeypatch):
    bodies = []
    _stub_provider(monkeypatch, bodies)
    op = {"model_id": "bytedance/seedance-2.5", "key_id": None, "_refs": ["data:image/png;base64,FOTO1", "data:image/png;base64,FOTO2"]}
    jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", op, "data:image/png;base64,CONT", {})
    refs = bodies[0][0]["input_references"]
    assert [r["type"] for r in refs] == ["video_url", "image_url", "image_url", "image_url"]
    ends = [r["image_url"]["url"].rsplit("base64,", 1)[1] for r in refs[1:]]
    assert ends == ["FOTO1", "FOTO2", "CONT"]  # fotos primeiro, continuidade por último


def test_model_that_refuses_photos_fails_loudly_instead_of_ignoring_the_character(monkeypatch):
    from app import providers
    bodies = []
    _stub_provider(monkeypatch, bodies, reject_refs=True)
    op = {"model_id": "x/y", "key_id": None, "_refs": ["data:image/png;base64,FOTO1"]}
    with pytest.raises(providers.ProviderError, match="imagens de referência"):
        jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", op)
    # sem fotos, nada muda: segue só com o vídeo
    bodies.clear()
    _stub_provider(monkeypatch, bodies, reject_refs=True)
    jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", {"model_id": "x/y", "key_id": None})
    assert len(bodies) == 1


@needs_ffmpeg
def test_api_validates_reference_ownership_and_stores_mode_and_refs(login, make_user, no_ai, monkeypatch):
    import io
    from PIL import Image
    c, me = _user_client(login, make_user)
    other = auth.public_user(make_user(f"ow-{time.time_ns()}@test.local"))
    udir = storage.user_dir(me["id"], "uploads")
    vid = storage.to_url(_clip(udir / f"v-{time.time_ns()}.mp4", 6))
    Image.new("RGB", (64, 64), (200, 30, 30)).save(udir / "roupa.png")
    mine = storage.to_url(udir / "roupa.png")
    Image.new("RGB", (64, 64)).save(storage.user_dir(other["id"], "uploads") / "alheia.png")
    theirs = storage.to_url(storage.user_dir(other["id"], "uploads") / "alheia.png")
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    fake = {"id": "img-fake", "kind": "image", "label": "x", "provider": "openrouter", "model_id": "x/y",
            "caps": {"t2i": True, "refs": True, "max_refs": 4}, "fields": {}, "params": {}, "cost": 0.034,
            "cost_unit": "run", "key_id": None, "key_name": "", "is_default": 1, "enabled": 1}
    monkeypatch.setattr(registry, "pick", lambda *a, **k: fake)
    monkeypatch.setattr(registry, "get", lambda mid, *a, **k: fake if mid == fake["id"] else None)
    assert c.post("/api/video/edit/quote", json={"source_url": vid, "whole": True, "ref_urls": [theirs]}).status_code == 403
    r = c.post("/api/video/edit", json={"source_url": vid, "whole": True, "mode": "motion", "ref_urls": [mine]})
    assert r.status_code == 200, r.text  # só a foto, sem prompt: as referências bastam
    op = started[0]["spec"]["local_op"]
    assert op["mode"] == "motion" and op["ref_urls"] == [mine]
    assert c.post("/api/video/edit", json={"source_url": vid, "whole": True, "mode": "xyz"}).status_code == 422
    assert c.post("/api/video/edit", json={"source_url": vid, "whole": True, "ref_urls": [mine] * 9}).status_code == 422


def test_node_has_the_R_port_mode_select_and_reference_block():
    from pathlib import Path
    js = Path(__file__).resolve().parents[1] / "static" / "js"
    ve = (js / "videoedit.js").read_text(encoding="utf-8")
    cv = (js / "canvas.js").read_text(encoding="utf-8")
    port_html = cv.split("n.type === 'videoedit' ? '")[1][:420]
    assert 'data-port="ref"' in port_html and "<i>R</i>" in port_html  # porta R própria do node
    assert "to.type === 'videoedit'" in cv  # a porta R aceita ligar personagem/roupa
    assert 'data-k="mode"' in ve and "VEDIT_MODES" in ve and "refsBlock(n," in ve and "bindRefs(el, n)" in ve
    assert "ref_urls: refsOf(n).slice(0, 8)" in ve and "mode: n.mode || 'swap'" in ve
    assert "n.preset !== 'none' || refsOf(n).length" in ve  # só as fotos já bastam para editar


# ---------- o 422 real do FLUX: "não pode continuar ou estender o vídeo" ----------

REAL_422 = ('HTTP 422: {"id":"19ae094e","status":"Error","result":null,"progress":null,"details":{"error":"This endpoint '
            'only edits the supplied MP4 video at its original duration; it cannot continue or extend the video. Describe '
            'changes to apply within the existing clip."},"preview":null}')


def test_prompts_never_ask_to_continue_or_extend():
    for mode in ("swap", "motion"):
        for refs in ([], ["/files/1/a.png"]):
            t = (videoedit.native_instruction({"mode": mode, "prompt": "troque por um canguru", "preset": "none",
                                               "strength": "medium", "ref_urls": refs}) + videoedit.CHAIN_NATIVE).lower()
            for w in videoedit.FORBIDDEN_EDIT_WORDS:
                assert w not in t, (mode, refs, w)


def test_provider_error_is_readable_not_raw_json():
    msg = jobs._provider_msg(REAL_422)
    assert msg.startswith("This endpoint only edits the supplied MP4") and "{" not in msg


def test_rejection_while_processing_retries_the_chunk_without_continuity(monkeypatch):
    """O caso do print: o envio é aceito e o provedor falha DEPOIS (status failed). Refaz sem a imagem e sem a frase."""
    from app import providers
    bodies, job = [], {}

    def submit(body, key_id=None):
        bodies.append(body)
        return {"id": f"v{len(bodies)}", "polling_url": "/x", "key_id": key_id, "with_ref": len(body["input_references"]) > 1}

    def status(h):
        if h["with_ref"]:
            return {"status": "failed", "error": REAL_422}
        return {"status": "completed", "usage": {"cost": 0.2}}

    monkeypatch.setattr(providers, "or_video_submit", submit)
    monkeypatch.setattr(providers, "or_video_status", status)
    monkeypatch.setattr(providers, "or_video_download", lambda h, s: b"ok")
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    data, cost = jobs._openrouter_video_edit({"key_id": None}, "Edit this video: canguru.", "https://h/pub/t/a.mp4",
                                             {"model_id": videoedit.NATIVE_MODEL, "key_id": None},
                                             "data:image/png;base64,CONT", job)
    assert data == b"ok" and cost == 0.2 and len(bodies) == 2
    assert videoedit.CHAIN_NATIVE in bodies[0]["prompt"] and len(bodies[0]["input_references"]) == 2
    assert bodies[1]["prompt"] == "Edit this video: canguru." and len(bodies[1]["input_references"]) == 1
    assert "refazendo o pedaço sem ela" in job["detail"] and "This endpoint" in job["detail"]


def test_without_continuity_a_rejection_surfaces_the_readable_message(monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, "or_video_submit", lambda b, k=None: {"id": "v", "polling_url": "/x", "key_id": k})
    monkeypatch.setattr(providers, "or_video_status", lambda h: {"status": "failed", "error": REAL_422})
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    with pytest.raises(providers.ProviderError) as e:
        jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", {"model_id": "x", "key_id": None})
    assert "o modelo recusou a edição: This endpoint only edits" in str(e.value) and '"id"' not in str(e.value)


@needs_ffmpeg
def test_cost_of_chunks_already_edited_is_kept_when_a_later_chunk_fails(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 30)
    dest = storage.user_dir(1, "renders") / f"part-{time.time_ns()}.mp4"
    n = {"i": 0}

    def fake(prompt, url, ref=None):
        n["i"] += 1
        if n["i"] == 3:
            raise RuntimeError("provedor recusou o 3º pedaço")
        return _color_clip(tmp_path / f"p{n['i']}.mp4", "red", 10), 0.3

    with pytest.raises(RuntimeError) as e:
        videoedit.run_native(src, {"start": 0, "end": 30, "prompt": "x", "preset": "none", "strength": "low",
                                   "preserve_audio": False, "user_id": 1}, dest, fake)
    assert abs(e.value.partial_cost - 0.6) < 1e-9  # 2 pedaços prontos e cobrados


def test_R_port_of_video_edit_has_its_own_position():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "studio.css").read_text(encoding="utf-8")
    assert ".cv-node[data-kind=videoedit] .cv-port.in.port-ref { top: 76px" in css


# ---------- emenda em vídeo (funciona até com modelos que ignoram imagem, como o FLUX) ----------

def test_chain_plan_single_chunk_with_hold_and_prefix_room():
    assert videoedit.chain_plan(0, 15.04, 14.8) == ([(0, 14.8)], 0.24)  # o vídeo do print: 1 pedaço, sem emenda
    assert videoedit.chain_plan(0, 14.0, 14.8) == ([(0, 14.0)], 0.0)
    ch, hold = videoedit.chain_plan(0, 30, 14.8)
    assert hold == 0.0 and len(ch) == 3 and ch[0][0] == 0 and ch[-1][1] == 30
    assert all((b - a) + videoedit.PREFIX_S <= 14.8 for a, b in ch[1:])  # com a emenda na frente ainda cabe no limite
    ch, _ = videoedit.chain_plan(0, 16, 14.8)
    assert len(ch) == 2  # sobra > 0,5 s: precisa de outro pedaço


def test_prefix_note_never_asks_to_continue():
    t = videoedit.PREFIX_NOTE.lower()
    assert all(w not in t for w in videoedit.FORBIDDEN_EDIT_WORDS)


def _two_color(path, c1, d1, c2, d2):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c={c1}:size=320x180:rate=25:duration={d1}",
                    "-f", "lavfi", "-i", f"color=c={c2}:size=320x180:rate=25:duration={d2}",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return path.read_bytes()


def _frame_rgb(video, t, out):
    subprocess.run([media._ff("ffmpeg"), "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                    str(out)], check=True, capture_output=True)
    return _mean_rgb(out.read_bytes())


@needs_ffmpeg
def test_next_chunk_receives_the_edited_tail_and_the_prefix_never_shows_in_the_result(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 30)
    dest = storage.user_dir(1, "renders") / f"pre-{time.time_ns()}.mp4"
    colors = ["red", "green", "blue"]
    rgb = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255)}
    c = TestClient(app, base_url=f"http://{HOST}")
    seen = []

    def fake(prompt, url, ref=None):
        k = len(seen)
        up = tmp_path / f"up{k}.mp4"
        up.write_bytes(_fetch(url, c))
        total = media.probe(up)["duration"]
        first = _frame_rgb(up, 0.1, tmp_path / f"f{k}.png")
        seen.append({"total": total, "first": first, "prompt": prompt})
        if k == 0:
            return _color_clip(tmp_path / f"o{k}.mp4", colors[k], total), 0.3
        # o "modelo" preserva a emenda (amarela, marcador) e edita o resto com a cor do pedaço
        return _two_color(tmp_path / f"o{k}.mp4", "yellow", round(total - 10, 3), colors[k], 10), 0.33

    videoedit.run_native(src, {"start": 0, "end": 30, "prompt": "canguru", "preset": "none", "strength": "medium",
                               "preserve_audio": False, "user_id": 1}, dest, fake)
    assert len(seen) == 3
    assert abs(seen[0]["total"] - 10) < 0.2  # 1º pedaço: só o original
    for k in (1, 2):  # do 2º em diante: ~1 s do resultado anterior na frente + 10 s originais
        assert abs(seen[k]["total"] - 11) < 0.2 and seen[k]["prompt"].endswith(videoedit.PREFIX_NOTE)
        want = rgb[colors[k - 1]]
        assert all(abs(a - b) < 60 for a, b in zip(seen[k]["first"], want)), (k, seen[k]["first"])
    out = media.probe(dest)
    assert abs(out["duration"] - 30) < 0.3  # a emenda é cortada: a duração não cresce
    for t, col in ((5, "red"), (10.3, "green"), (15, "green"), (20.3, "blue"), (25, "blue")):
        got = _frame_rgb(dest, t, tmp_path / f"r{t}.png")
        assert all(abs(a - b) < 70 for a, b in zip(got, rgb[col])), (t, got)  # nunca o amarelo da emenda


@needs_ffmpeg
def test_video_slightly_over_the_limit_is_one_chunk_with_the_last_frame_held(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 15.04)
    dest = storage.user_dir(1, "renders") / f"hold-{time.time_ns()}.mp4"
    calls = []

    def fake(prompt, url, ref=None):
        calls.append(ref)
        return _color_clip(tmp_path / "o.mp4", "red", 14.8), 0.44

    videoedit.run_native(src, {"start": 0, "end": 15.04, "prompt": "x", "preset": "none", "strength": "low",
                               "preserve_audio": True, "user_id": 1, "chunk_s": 14.8}, dest, fake)
    assert calls == [None]  # um envio só, sem emenda
    assert abs(media.probe(dest)["duration"] - 15.04) < 0.15
    end = _frame_rgb(dest, 15.0, tmp_path / "fim.png")
    assert end[0] > 180 and end[1] < 80  # o finalzinho é o quadro editado (vermelho), não o original


@needs_ffmpeg
def test_tiny_tail_after_the_segment_does_not_paste_the_whole_original_again(tmp_path):
    src = _clip(tmp_path / "fonte.mp4", 6)
    dest = storage.user_dir(1, "renders") / f"tail-{time.time_ns()}.mp4"
    videoedit.run_native(src, {"start": 0, "end": 5.85, "prompt": "x", "preset": "none", "strength": "low",
                               "preserve_audio": False, "user_id": 1},
                         dest, lambda p, u, ref=None: (_color_clip(tmp_path / "o.mp4", "red", 6), 0.1))
    assert abs(media.probe(dest)["duration"] - 6) < 0.2  # antes: sobra < 0,3 s colava o original inteiro (12 s)


def test_swap_keeps_every_other_object_even_ones_that_appear_later():
    t = videoedit.native_instruction({"mode": "swap", "prompt": "troque a abelha por um canguru", "preset": "none",
                                      "strength": "medium", "ref_urls": []})
    assert "including ones that only appear partway through the clip" in t
    assert all(w not in t.lower() for w in videoedit.FORBIDDEN_EDIT_WORDS)
    motion = videoedit.native_instruction({"mode": "motion", "prompt": "", "preset": "none", "strength": "medium",
                                           "ref_urls": ["/files/1/a.png"]})
    assert videoedit.KEEP_OBJECTS not in motion  # transferir movimento refaz a cena: não faz sentido travar objetos


def test_wan_is_in_the_list_without_registering_it(make_user):
    user = auth.public_user(make_user(f"wan-{time.time_ns()}@test.local"))
    ms = {m["id"]: m for m in videoedit.native_models(user)}
    assert "wan" in ms and ms["wan"]["model_id"] == "alibaba/wan-2.7" and ms["wan"]["generic"] is True
    assert ms["wan"]["chunk_s"] == 10.0 and ms["wan"]["usd_per_s"] == 0.10
    assert "alibaba/wan-2.7" in videoedit.VIDEO_INPUT_MODELS  # recebe vídeo: pode estar neste node
    assert videoedit.choose_model(user, "wan")["model_id"] == "alibaba/wan-2.7"


def test_wan_request_has_duration_aspect_resolution_and_the_video(monkeypatch):
    bodies = []
    _stub_provider(monkeypatch, bodies)
    op = {"model_id": "alibaba/wan-2.7", "key_id": None, "generic": True, "reg_id": None,
          "_aspect": "9:16", "_chunk_len": 9.6}
    jobs._openrouter_video_edit({"key_id": None}, "troque a abelha", "https://h/pub/t/a.mp4", op)
    body = bodies[0][0]
    assert body["model"] == "alibaba/wan-2.7" and body["duration"] == 10 and body["aspect_ratio"] == "9:16"
    assert body["resolution"] == "720p"
    assert body["input_references"] == [{"type": "video_url", "video_url": {"url": "https://h/pub/t/a.mp4"}}]
    bodies.clear()
    jobs._openrouter_video_edit({"key_id": None}, "p", "https://h/pub/t/a.mp4", {**op, "_aspect": "21:9", "_chunk_len": 3.2})
    assert bodies[0][0]["duration"] == 4 and bodies[0][0]["aspect_ratio"] == "16:9"  # 3,2 s -> 4 s (cobre); proporção padrão


def test_wan_chunks_fit_its_10_second_limit_with_the_prefix():
    ch, hold = videoedit.chain_plan(0, 30, 10.0)
    assert hold == 0.0 and len(ch) == 4  # 30 s: 4 pedaços de 7,5 s (+1 s de emenda nos 3 últimos)
    assert all((b - a) + videoedit.PREFIX_S <= 10.0 for a, b in ch[1:])
