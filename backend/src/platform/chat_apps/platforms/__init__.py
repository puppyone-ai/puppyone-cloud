"""Chat platform adapters, built from settings in one place."""

from __future__ import annotations

from typing import Any

from ..ports import ChatPlatform


def build_platforms(settings: Any) -> dict[str, ChatPlatform]:
    """Only the platforms named in ``CHAT_APPS_PLATFORMS`` exist in this process."""
    wanted = settings.chat_app_platforms
    built: dict[str, ChatPlatform] = {}
    if "telegram" in wanted:
        from .telegram import TelegramPlatform

        built["telegram"] = TelegramPlatform(
            bot_token=settings.CHAT_APPS_TELEGRAM_BOT_TOKEN,
            bot_username=settings.CHAT_APPS_TELEGRAM_BOT_USERNAME,
            webhook_secret=settings.CHAT_APPS_TELEGRAM_WEBHOOK_SECRET,
            api_base=settings.CHAT_APPS_TELEGRAM_API_BASE,
        )
    if "slack" in wanted:
        from .slack import SlackPlatform

        built["slack"] = SlackPlatform(
            client_id=settings.CHAT_APPS_SLACK_CLIENT_ID,
            client_secret=settings.CHAT_APPS_SLACK_CLIENT_SECRET,
            signing_secret=settings.CHAT_APPS_SLACK_SIGNING_SECRET,
            public_api_url=settings.PUBLIC_URL,
            api_base=settings.CHAT_APPS_SLACK_API_BASE,
            authorize_base=settings.CHAT_APPS_SLACK_AUTHORIZE_URL,
        )
    if "wecom" in wanted:
        from .wecom import WeComPlatform

        built["wecom"] = WeComPlatform(ws_url=settings.CHAT_APPS_WECOM_WS_URL)
    return built
