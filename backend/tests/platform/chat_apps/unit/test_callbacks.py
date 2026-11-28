"""A release only sets PUBLIC_URL: the gateway points Telegram and the Slack app at it."""

import asyncio
import base64
import json
from urllib.parse import parse_qs

import httpx

from src.config import settings
from src.platform.chat_apps import callbacks, credentials

KEY = base64.b64encode(bytes(range(32))).decode()  # a fixed test key, made here
URL = "https://api.example.test"
MANIFEST = {
    "display_information": {"name": "PuppyOne"},
    "oauth_config": {"redirect_urls": ["https://old.example/x"], "scopes": {"bot": ["chat:write"]}},
    "settings": {
        "event_subscriptions": {
            "request_url": "https://old.example/e",
            "bot_events": ["app_mention"],
        },
        "interactivity": {"is_enabled": True, "request_url": "https://old.example/a"},
        "socket_mode_enabled": False,
    },
}


def test_only_the_three_callback_urls_change():
    pointed = callbacks.pointed_manifest(MANIFEST, URL + "/")
    assert pointed["settings"]["event_subscriptions"] == {
        "request_url": URL + "/api/v1/chat-apps/slack/events",
        "bot_events": ["app_mention"],
    }
    assert pointed["settings"]["interactivity"]["request_url"].endswith("/slack/actions")
    assert pointed["oauth_config"] == {
        "redirect_urls": [URL + "/api/v1/chat-apps/slack/oauth/callback"],
        "scopes": {"bot": ["chat:write"]},
    }
    assert pointed["display_information"] == MANIFEST["display_information"]
    assert callbacks.pointed_manifest(pointed, URL) == pointed


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


class Tokens:
    def __init__(self, stored=None):
        self.row = stored
        self.saves = []

    def gateway(self, action, worker, **payload):
        if action == "platform_token":
            return self.row
        self.saves.append(payload)
        return {"saved": True}


def test_slack_rotates_keeps_the_new_token_and_updates_once(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_APPS_ENCRYPTION_KEY", KEY)
    state = {"manifest": MANIFEST, "refresh": "r1", "updates": 0}

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        form = parse_qs(request.content.decode())
        if method == "tooling.tokens.rotate":
            if form["refresh_token"][0] != state["refresh"]:
                return httpx.Response(200, json={"ok": False, "error": "invalid_refresh_token"})
            state["refresh"] = state["refresh"] + "+"
            return httpx.Response(
                200, json={"ok": True, "token": "a", "refresh_token": state["refresh"]}
            )
        if method == "apps.manifest.export":
            return httpx.Response(200, json={"ok": True, "manifest": state["manifest"]})
        state["manifest"] = json.loads(form["manifest"][0])
        state["updates"] += 1
        return httpx.Response(200, json={"ok": True})

    tokens = Tokens()

    async def once():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await callbacks.sync_slack(
                http,
                repository=tokens,
                worker="w",
                api_base="https://slack.test/api",
                app_id="A1",
                bootstrap="r1",
                public_url=URL,
            )

    assert asyncio.run(once()) == "updated"
    saved = tokens.saves[-1]
    assert saved["version"] is None and credentials.unseal(saved["secret"]) == "r1+"
    # The next start rotates the stored token, not the used-up bootstrap one.
    tokens.row = {"secret": saved["secret"], "version": 1}
    assert asyncio.run(once()) == "unchanged" and state["updates"] == 1
    assert tokens.saves[-1]["version"] == 1


def test_nothing_happens_without_an_https_public_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "http://localhost:9090")
    result = asyncio.run(callbacks.sync_callbacks(settings, {"telegram": object()}, Tokens(), "w"))
    assert result == {"skipped": "PUBLIC_URL is not an https origin"}
