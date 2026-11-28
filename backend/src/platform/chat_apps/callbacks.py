"""Keep each chat platform pointed at this deployment's public API.

The gateway runs this on start, so a release only sets PUBLIC_URL: Telegram's
webhook and the Slack app's event, interactivity and OAuth redirect URLs follow
it. Nothing is written when they already match. Slack's app configuration
refresh token is single use: the new one is sealed in the database before the
manifest is touched, and a concurrent rotation by another instance wins.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from src.infra.execution.runner import run_blocking
from src.platform.chat_apps import credentials

logger = logging.getLogger(__name__)

PREFIX = "/api/v1/chat-apps"
TELEGRAM_UPDATES = ["message", "callback_query", "my_chat_member", "guest_message"]
SLACK_TOKEN = "slack_config"


class CallbackError(RuntimeError):
    pass


def slack_urls(public_url: str) -> dict[str, str]:
    base = public_url.rstrip("/") + PREFIX + "/slack"
    return {
        "events": base + "/events",
        "actions": base + "/actions",
        "redirect": base + "/oauth/callback",
    }


def pointed_manifest(manifest: dict[str, Any], public_url: str) -> dict[str, Any]:
    """The manifest with only its three callback URLs set to ``public_url``."""
    urls = slack_urls(public_url)
    settings = dict(manifest.get("settings") or {})
    events = dict(settings.get("event_subscriptions") or {})
    events["request_url"] = urls["events"]
    interactivity = dict(settings.get("interactivity") or {})
    interactivity.update(is_enabled=True, request_url=urls["actions"])
    oauth = dict(manifest.get("oauth_config") or {})
    oauth["redirect_urls"] = [urls["redirect"]]
    return {
        **manifest,
        "settings": {**settings, "event_subscriptions": events, "interactivity": interactivity},
        "oauth_config": oauth,
    }


async def _slack(http: httpx.AsyncClient, api: str, method: str, token: str | None, **data):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = (await http.post(f"{api}/{method}", data=data, headers=headers)).json()
    if not body.get("ok"):
        raise CallbackError(f"slack {method}: {body.get('error')}")
    return body


async def sync_telegram(http, *, api_base, bot_token, secret, public_url) -> str:
    url = public_url.rstrip("/") + PREFIX + "/telegram/webhook"
    base = f"{api_base.rstrip('/')}/bot{bot_token}"
    info = (await http.get(f"{base}/getWebhookInfo")).json().get("result") or {}
    if info.get("url") == url and sorted(info.get("allowed_updates") or []) == sorted(
        TELEGRAM_UPDATES
    ):
        return "unchanged"
    body = (
        await http.post(
            f"{base}/setWebhook",
            json={"url": url, "secret_token": secret, "allowed_updates": TELEGRAM_UPDATES},
        )
    ).json()
    if not body.get("ok"):
        raise CallbackError(f"telegram setWebhook: {body.get('description')}")
    return "updated"


async def _config_token(http, repository, worker, api, bootstrap: str) -> str:
    """Rotate the refresh token for a 12-hour access token; keep the new one."""
    stored = await run_blocking(repository.gateway, "platform_token", worker, name=SLACK_TOKEN)
    candidates = [credentials.unseal(stored["secret"])] if stored else []
    if bootstrap and bootstrap not in candidates:
        candidates.append(bootstrap)  # first run, or a freshly generated token
    last = None
    for refresh in candidates:
        try:
            body = await _slack(http, api, "tooling.tokens.rotate", None, refresh_token=refresh)
        except CallbackError as exc:
            last = exc
            continue
        await run_blocking(
            repository.gateway,
            "save_platform_token",
            worker,
            name=SLACK_TOKEN,
            secret=credentials.seal(body["refresh_token"]),
            version=stored["version"] if stored else None,
        )
        return body["token"]
    raise last or CallbackError("slack: no app configuration token")


async def sync_slack(http, *, repository, worker, api_base, app_id, bootstrap, public_url) -> str:
    api = api_base.rstrip("/")
    token = await _config_token(http, repository, worker, api, bootstrap)
    manifest = (await _slack(http, api, "apps.manifest.export", token, app_id=app_id))["manifest"]
    wanted = pointed_manifest(manifest, public_url)
    if wanted == manifest:
        return "unchanged"
    await _slack(
        http, api, "apps.manifest.update", token, app_id=app_id, manifest=json.dumps(wanted)
    )
    return "updated"


async def sync_callbacks(settings, platforms, repository, worker, *, http=None) -> dict[str, str]:
    """Point every configured platform at PUBLIC_URL; one failure never stops the rest."""
    public_url = (settings.PUBLIC_URL or "").strip()
    if not public_url.startswith("https://"):
        return {"skipped": "PUBLIC_URL is not an https origin"}
    results: dict[str, str] = {}
    async with http or httpx.AsyncClient(timeout=20) as client:
        jobs = []
        if "telegram" in platforms and settings.CHAT_APPS_TELEGRAM_BOT_TOKEN:
            jobs.append(
                (
                    "telegram",
                    lambda: sync_telegram(
                        client,
                        api_base=settings.CHAT_APPS_TELEGRAM_API_BASE,
                        bot_token=settings.CHAT_APPS_TELEGRAM_BOT_TOKEN,
                        secret=settings.CHAT_APPS_TELEGRAM_WEBHOOK_SECRET,
                        public_url=public_url,
                    ),
                )
            )
        if "slack" in platforms and settings.CHAT_APPS_SLACK_APP_ID:
            jobs.append(
                (
                    "slack",
                    lambda: sync_slack(
                        client,
                        repository=repository,
                        worker=worker,
                        api_base=settings.CHAT_APPS_SLACK_API_BASE,
                        app_id=settings.CHAT_APPS_SLACK_APP_ID,
                        bootstrap=settings.CHAT_APPS_SLACK_CONFIG_REFRESH_TOKEN,
                        public_url=public_url,
                    ),
                )
            )
        for name, job in jobs:
            try:
                results[name] = await job()
            except Exception as exc:  # the gateway still serves; it retries, then the next start
                results[name] = "failed"
                error = str(exc)
                for secret in (settings.CHAT_APPS_TELEGRAM_BOT_TOKEN,):
                    error = error.replace(secret, "***") if secret else error
                logger.warning(
                    "chat_app_callbacks_failed",
                    extra={"chat_app": {"platform": name, "error": error[:200]}},
                )
    logger.info("chat_app_callbacks", extra={"chat_app": results})
    return results
