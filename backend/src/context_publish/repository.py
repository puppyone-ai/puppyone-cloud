from __future__ import annotations

from abc import ABC, abstractmethod

from src.context_publish.models import ContextPublish
from src.context_publish.supabase_schemas import (
    ContextPublishCreate as SbContextPublishCreate,
)
from src.context_publish.supabase_schemas import (
    ContextPublishUpdate as SbContextPublishUpdate,
)
from src.context_publish.supabase_repo import ContextPublishRepository


class ContextPublishRepositoryBase(ABC):
    @abstractmethod
    def create(self, data: SbContextPublishCreate) -> ContextPublish:
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, publish_id: int) -> ContextPublish | None:
        raise NotImplementedError

    @abstractmethod
    def get_by_key(self, publish_key: str) -> ContextPublish | None:
        raise NotImplementedError

    @abstractmethod
    def list_by_created_by(
        self, created_by: str, *, skip: int = 0, limit: int = 100
    ) -> list[ContextPublish]:
        raise NotImplementedError

    @abstractmethod
    def update(
        self, publish_id: int, data: SbContextPublishUpdate
    ) -> ContextPublish | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, publish_id: int) -> bool:
        raise NotImplementedError


class ContextPublishRepositorySupabase(ContextPublishRepositoryBase):
    def __init__(self, repository: ContextPublishRepository):
        self._repo = repository

    def _to_model(self, resp) -> ContextPublish:
        return ContextPublish(
            id=resp.id,
            created_at=resp.created_at,
            updated_at=resp.updated_at,
            created_by=str(resp.created_by) if resp.created_by else None,
            table_id=str(resp.table_id or ""),
            json_path=resp.json_path or "",
            publish_key=resp.publish_key or "",
            status=bool(resp.status),
            expires_at=resp.expires_at,
        )

    def create(self, data: SbContextPublishCreate) -> ContextPublish:
        resp = self._repo.create(data)
        return self._to_model(resp)

    def get_by_id(self, publish_id: int) -> ContextPublish | None:
        resp = self._repo.get_by_id(publish_id)
        if not resp:
            return None
        return self._to_model(resp)

    def get_by_key(self, publish_key: str) -> ContextPublish | None:
        resp = self._repo.get_by_publish_key(publish_key)
        if not resp:
            return None
        return self._to_model(resp)

    def list_by_created_by(
        self, created_by: str, *, skip: int = 0, limit: int = 100
    ) -> list[ContextPublish]:
        resps = self._repo.get_list(
            skip=skip, limit=limit, created_by=created_by
        )
        return [self._to_model(r) for r in resps]

    def update(
        self, publish_id: int, data: SbContextPublishUpdate
    ) -> ContextPublish | None:
        resp = self._repo.update(publish_id, data)
        if not resp:
            return None
        return self._to_model(resp)

    def delete(self, publish_id: int) -> bool:
        return self._repo.delete(publish_id)
