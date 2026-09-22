from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import settings
from src.content.table.service import TableService
from src.context_publish.cache import PublishCache
from src.context_publish.models import ContextPublish
from src.context_publish.repository import ContextPublishRepositoryBase
from src.context_publish.supabase_schemas import (
    ContextPublishCreate as SbContextPublishCreate,
)
from src.context_publish.supabase_schemas import (
    ContextPublishUpdate as SbContextPublishUpdate,
)
from src.exceptions import ErrorCode, NotFoundException, ValidationException
from src.infra.supabase.exceptions import SupabaseDuplicateKeyError, SupabaseException
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService

_BASE62_ALPHABET = string.ascii_letters + string.digits


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _default_expires_at() -> datetime:
    return _now_utc() + timedelta(days=int(settings.PUBLISH_DEFAULT_EXPIRES_DAYS))


def _generate_publish_key(*, length: int) -> str:
    # Fixed-length base62 random string: URL-safe, non-guessable
    length = int(length)
    if length <= 0:
        raise ValueError("publish_key length must be positive")
    return "".join(secrets.choice(_BASE62_ALPHABET) for _ in range(length))


class ContextPublishService:
    def __init__(
        self,
        *,
        repo: ContextPublishRepositoryBase,
        table_service: TableService,
        authorization: AuthorizationService,
    ):
        self.repo = repo
        self.table_service = table_service
        self.authorization = authorization
        self.cache = PublishCache(ttl_seconds=int(settings.PUBLISH_CACHE_TTL_SECONDS))

    def _invalidate_cache(self, publish_key: str) -> None:
        self.cache.invalidate(publish_key)

    def _authorize_publish_management(self, table_id: str, user_id: str) -> None:
        """Authorize disclosure of Project content through a public link.

        Publish ownership is a child-resource restriction, not a replacement
        for the current Project grant.  A revoked member must not keep managing
        an old public link merely because they created it in the past.
        """
        table = self.table_service.get_by_id(table_id)
        if table is None or not table.project_id:
            raise NotFoundException("Table not found", code=ErrorCode.NOT_FOUND)
        self.authorization.authorize(
            table.project_id,
            user_id,
            ProjectAction.SHARE_MANAGE,
        )

    def create(
        self,
        *,
        created_by: str,
        table_id: str,
        json_path: str,
        expires_at: datetime | None,
    ) -> ContextPublish:
        self._authorize_publish_management(table_id, created_by)

        if expires_at is None:
            expires_at = _default_expires_at()

        # Generate key (globally unique: only retry on "unique conflict", other errors should expose the real cause)
        last_dup: Exception | None = None
        for _ in range(10):
            key = _generate_publish_key(length=int(settings.PUBLISH_KEY_LENGTH))
            try:
                created = self.repo.create(
                    SbContextPublishCreate(
                        created_by=created_by,
                        table_id=table_id,
                        json_path=json_path or "",
                        publish_key=key,
                        status=True,
                        expires_at=expires_at,
                    )
                )
                self.cache.set(created.publish_key, created)
                return created
            except SupabaseDuplicateKeyError as e:
                # Only retry on publish_key unique conflict; other field conflicts should not occur.
                last_dup = e
                continue
            except SupabaseException:
                # E.g.: table not created, RLS/permissions, field/type mismatch, etc. -- raise directly, avoid being masked by "unique retry"
                raise
            except Exception:
                raise

        # Extremely low probability: consecutive key collisions; return 422 (Validation)
        raise ValidationException("Failed to generate unique publish_key") from last_dup

    def list_by_created_by(
        self, created_by: str, *, skip: int = 0, limit: int = 100
    ) -> list[ContextPublish]:
        items = self.repo.list_by_created_by(
            created_by, skip=skip, limit=limit
        )
        visible: list[ContextPublish] = []
        for item in items:
            try:
                self._authorize_publish_management(item.table_id, created_by)
            except Exception:
                # List endpoints must fail closed per resource: an old publish
                # must not leak after Project access loss or fact-read failure.
                continue
            visible.append(item)
        return visible

    def get_by_id_with_access_check(
        self, publish_id: int, created_by: str
    ) -> ContextPublish:
        p = self.repo.get_by_id(publish_id)
        if not p or (p.created_by or "") != created_by:
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)
        self._authorize_publish_management(p.table_id, created_by)
        return p

    def update(
        self,
        *,
        publish_id: int,
        created_by: str,
        status: bool | None,
        expires_at: datetime | None,
    ) -> ContextPublish:
        existing = self.get_by_id_with_access_check(publish_id, created_by)
        updated = self.repo.update(
            publish_id,
            SbContextPublishUpdate(status=status, expires_at=expires_at),
        )
        if not updated:
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)
        # Proactively invalidate cache (per spec: takes effect immediately)
        self._invalidate_cache(existing.publish_key)
        return updated

    def delete(self, *, publish_id: int, created_by: str) -> None:
        existing = self.get_by_id_with_access_check(publish_id, created_by)
        ok = self.repo.delete(publish_id)
        if not ok:
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)
        self._invalidate_cache(existing.publish_key)

    def _get_by_key_cached(self, publish_key: str) -> ContextPublish | None:
        cached = self.cache.get(publish_key)
        if cached:
            return cached
        found = self.repo.get_by_key(publish_key)
        if found:
            self.cache.set(publish_key, found)
        return found

    def get_public_json(self, publish_key: str) -> Any:
        p = self._get_by_key_cached(publish_key)
        if not p:
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)
        if not p.status:
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)
        if p.expires_at and p.expires_at <= _now_utc():
            # Expired is treated as non-existent
            raise NotFoundException("Publish not found", code=ErrorCode.NOT_FOUND)

        # Note: public read does not perform user permission checks; publish_key serves as the access credential
        data = self.table_service.get_context_data(
            table_id=p.table_id, json_pointer_path=p.json_path or ""
        )
        return data
