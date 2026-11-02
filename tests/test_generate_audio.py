"""Gerar áudio no node de vídeo: o parâmetro vai ao provedor só quando o modelo suporta e o usuário marcou."""
import asyncio
import time

from fastapi.testclient import TestClient

from app import auth, jobs, providers, registry, storage, usage
from app.main import app
from app import registry as reg
from app.jobs import RenderRequest


def _uniq():
    return str(time.time_ns())


def _model(audio: bool):
    return {"id": 977, "kind": "video", "label": "Vídeo teste", "provider": "openrouter", "model_id": "x/video",
            "caps": {"prompt": True, "i2v": True, "audio": audio, "durations": [4.0, 8.0]},
            "fields": {"api": "openrouter_video", "resolutions": ["480p", "720p"], "aspects": ["16:9"]},
            "params": {}, "cost": 0.05, "cost_unit": "second", "key_id": None, "key_name": "", "is_default": 0,
            "enabled": 1, "available": True}


def _body(caps_audio, generate_audio):
    m = {"model_id": "x/video", "caps": {"audio": caps_audio, "durations": [4.0]},
         "fields": {"resolutions": ["480p"], "aspects": ["16:9"]}, "params": {}}
    return reg.build_or_video_input(m, prompt="p", start=None, end=None, refs=[], aspect="16:9", duration=4,
                                    quality="draft", seed=None, generate_audio=generate_audio)


def test_request_defaults_to_no_audio():
    assert RenderRequest(prompt="p").generate_audio is False


def test_audio_is_sent_when_the_model_supports_it_and_the_user_asked():
    assert _body(True, True)["generate_audio"] is True


def test_audio_is_explicitly_off_when_unchecked_for_an_audio_model():
    assert _body(True, False)["generate_audio"] is False  # não paga por áudio sem pedir


def test_model_without_audio_support_ignores_the_flag():
    assert "generate_audio" not in _body(False, True)


def test_end_to_end_body_sent_to_the_provider(login, make_user, monkeypatch):
    monkeypatch.setattr(usage, "check", lambda *a, **k: None)
    fake = _model(True)
    monkeypatch.setattr(registry, "for_user", lambda user, kind=None: [fake])
    monkeypatch.setattr(registry, "get", lambda mid, *a, **k: fake if mid == fake["id"] else None)

    async def aget(mid, *a, **k):
        return fake if mid == fake["id"] else None
    monkeypatch.setattr(registry, "aget", aget)
    sent = []

    def submit(body, key_id=None):  # captura o corpo que iria ao provedor e para aqui
        sent.append(body)
        raise providers.ProviderError("parou no teste")
    monkeypatch.setattr(providers, "or_video_submit", submit)
    started = []
    monkeypatch.setattr(jobs, "start", lambda job, user, resume=False: started.append(job))
    u = make_user(f"gaud-{_uniq()}@test.local")
    c = login(u["email"], c=TestClient(app))
    me = auth.public_user(u)
    from PIL import Image
    path = storage.user_dir(me["id"], "uploads") / f"f-{_uniq()}.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(path)
    img = storage.to_url(path)
    for flag, expected in ((True, True), (False, False)):
        sent.clear(); started.clear()
        r = c.post("/api/render", json={"node_id": "n", "name": "n", "gen": "video", "model": fake["id"], "prompt": "p",
                                        "duration": 4, "quality": "draft", "start_image_url": img, "generate_audio": flag})
        assert r.status_code == 200, r.text
        asyncio.run(jobs._run(started[0], me))  # o job roda até o envio ao provedor
        assert sent and sent[0].get("generate_audio") is expected, sent


def test_front_end_offers_the_toggle_only_for_audio_models():
    from app import config
    src = (config.STATIC_DIR / "js" / "canvas.js").read_text(encoding="utf-8")
    assert "const videoSupportsAudio = (n) => !!videoPick(n)?.caps?.audio;" in src
    assert "${audioToggle(n)}" in src and "data-k=\"genAudio\"" in src
    assert "generate_audio: n.gen !== 'image' && !!n.genAudio && videoSupportsAudio(n)" in src
    assert "if (inp.type === 'checkbox') n[inp.dataset.k] = inp.checked;" in src  # checkbox guarda booleano
