"""A release only sets PUBLIC_URL: the gateway points Telegram's webhook at it."""

import asyncio
import json

import httpx

from src.config import settings
from src.platform.chat_apps import callbacks

URL = "https://api.example.test"


def test_telegram_is_set_only_when_it_points_elsewhere():
    seen = []
    current = {"url": "", "allowed_updates": []}

    def handler(request):
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("getWebhookInfo"):
            return httpx.Response(200, json={"ok": True, "result": current})
        current.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            kw = dict(api_base="https://tg.test", bot_token="t0k", secret="s", public_url=URL)
            return [await callbacks.sync_telegram(http, **kw) for _ in range(2)]

    assert asyncio.run(run()) == ["updated", "unchanged"]
    assert seen == ["getWebhookInfo", "setWebhook", "getWebhookInfo"]
    assert current["url"] == URL + "/api/v1/chat-apps/telegram/webhook"


def test_nothing_happens_without_an_https_public_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "http://localhost:9090")
    result = asyncio.run(callbacks.sync_callbacks(settings, {"telegram": object()}))
    assert result == {"skipped": "PUBLIC_URL is not an https origin"}
