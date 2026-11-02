"""Polling de vídeo tem teto: passou do prazo, o job falha com timeout em vez de girar para sempre."""
import asyncio
import time

import pytest

from app import jobs, providers


def test_video_polling_gives_up_after_deadline(monkeypatch):
    def never_called(*args, **kwargs):
        raise AssertionError("não deveria consultar o provedor depois do prazo")

    monkeypatch.setattr(providers, "or_video_status", never_called)
    st = {"handle": {"polling_url": "/api/v1/videos/x"}, "started": time.time() - jobs.VIDEO_TIMEOUT_S - 60}
    with pytest.raises(providers.ProviderError, match="timeout"):
        asyncio.run(jobs._or_video({}, st, {}, 0, 1, "p", None, None))
