from __future__ import annotations

from functools import lru_cache

from src.infra.s3.service import get_s3_service_instance
from src.platform.office.config import OfficeSettings, office_settings
from src.platform.office.service import ManagedOfficeService
from src.platform.office.store import RedisOfficeSessionStore


@lru_cache(maxsize=1)
def get_office_session_store() -> RedisOfficeSessionStore:
    return RedisOfficeSessionStore(office_settings.PUPPYONE_OFFICE_REDIS_URL)


def get_office_settings() -> OfficeSettings:
    return office_settings


@lru_cache(maxsize=1)
def get_office_service_instance() -> ManagedOfficeService:
    return ManagedOfficeService(
        config=office_settings,
        sessions=(
            get_office_session_store()
            if office_settings.PUPPYONE_OFFICE_ENABLED
            else _DisabledOfficeSessionStore()
        ),
        binaries=get_s3_service_instance(),
    )


def get_office_service() -> ManagedOfficeService:
    # Keep the HTTP pool and Redis client process-wide while retaining explicit
    # FastAPI dependency hooks for tests and application composition.
    return get_office_service_instance()


class _DisabledOfficeSessionStore:
    def __getattr__(self, _name):
        async def unavailable(*_args, **_kwargs):
            raise RuntimeError("Managed Office editing is disabled")

        return unavailable
