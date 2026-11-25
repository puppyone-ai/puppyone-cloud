"""Depends providers. Platforms are built once per process from settings."""

from functools import cache

from src.config import settings
from src.platform.agent.dependencies import get_agent_service
from src.platform.chat_apps.platforms import build_platforms
from src.platform.chat_apps.repository import ChatAppRepository
from src.platform.chat_apps.service import ChatAppService


@cache
def get_platforms() -> dict:
    return build_platforms(settings)


def get_chat_app_repository() -> ChatAppRepository:
    return ChatAppRepository()


def mentions() -> dict[str, str]:
    username = settings.CHAT_APPS_TELEGRAM_BOT_USERNAME.lstrip("@")
    return {"telegram": "@" + username} if username else {}


def get_chat_app_service() -> ChatAppService:
    return ChatAppService(
        get_chat_app_repository(), get_platforms(), get_agent_service(), mentions=mentions()
    )
