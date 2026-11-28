"""Keep Telegram pointed at this deployment's public API.

The gateway runs this on start, so a release only sets PUBLIC_URL for
Telegram's webhook. Nothing is written when it already matches. Slack's event,
interactivity and OAuth redirect URLs are fixed per environment and are set
once in the Slack app's settings (the deploy checklist), so nothing about the
Slack app is stored or rotated here.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

PREFIX = "/api/v1/chat-apps"
TELEGRAM_UPDATES = ["message", "callback_query", "my_chat_member", "guest_message"]


class CallbackError(RuntimeError):
    pass


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


async def sync_callbacks(settings, platforms, *, http=None) -> dict[str, str]:
    """Point Telegram at PUBLIC_URL; a failure is logged and retried by the caller."""
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
