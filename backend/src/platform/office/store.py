from __future__ import annotations

import time
from typing import Protocol

from src.platform.office.models import OfficeSessionRecord, OfficeStatus


class OfficeSessionStoreUnavailable(RuntimeError):
    pass


class OfficeSessionStore(Protocol):
    async def create(self, record: OfficeSessionRecord, ttl_seconds: int) -> bool: ...
    async def get(self, session_id: str) -> OfficeSessionRecord | None: ...
    async def set_status(
        self,
        session_id: str,
        status: OfficeStatus,
        *,
        message: str = "",
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None: ...
    async def publish_result(
        self,
        session_id: str,
        *,
        result_sha256: str,
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None: ...
    async def count_for_owner(self, owner_user_id: str) -> int: ...
    async def delete(self, session_id: str) -> OfficeSessionRecord | None: ...


_SET_STATUS_LUA = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return nil end
redis.call('HSET', key, 'status', ARGV[1], 'message', ARGV[2], 'updated_at_ms', ARGV[3])
redis.call('EXPIRE', key, ARGV[4])
return redis.call('HGETALL', key)
"""

_PUBLISH_RESULT_LUA = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return nil end
local previous = redis.call('HGET', key, 'result_sha256') or ''
if previous ~= ARGV[1] then
  redis.call('HINCRBY', key, 'result_revision', 1)
end
redis.call('HSET', key, 'result_sha256', ARGV[1], 'status', 'saved', 'message', '', 'updated_at_ms', ARGV[2])
redis.call('EXPIRE', key, ARGV[3])
return redis.call('HGETALL', key)
"""

_DELETE_LUA = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return nil end
local value = redis.call('HGETALL', key)
redis.call('DEL', key)
return value
"""


class RedisOfficeSessionStore:
    def __init__(self, redis_url: str):
        if not redis_url:
            raise OfficeSessionStoreUnavailable("PUPPYONE_OFFICE_REDIS_URL is not configured")
        try:
            import redis.asyncio as redis

            self._redis = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            self._set_status_script = self._redis.register_script(_SET_STATUS_LUA)
            self._publish_result_script = self._redis.register_script(_PUBLISH_RESULT_LUA)
            self._delete_script = self._redis.register_script(_DELETE_LUA)
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to initialize managed Office Redis") from exc

    @staticmethod
    def _key(session_id: str) -> str:
        return f"managed-office:session:{session_id}"

    async def create(self, record: OfficeSessionRecord, ttl_seconds: int) -> bool:
        try:
            created = await self._redis.hset(self._key(record.session_id), mapping=_serialize(record))
            await self._redis.expire(self._key(record.session_id), ttl_seconds)
            return bool(created)
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to persist managed Office session") from exc

    async def get(self, session_id: str) -> OfficeSessionRecord | None:
        try:
            value = await self._redis.hgetall(self._key(session_id))
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to read managed Office session") from exc
        return _deserialize(value)

    async def set_status(
        self,
        session_id: str,
        status: OfficeStatus,
        *,
        message: str = "",
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None:
        try:
            value = await self._set_status_script(
                keys=[self._key(session_id)],
                args=[status, message, _now_ms(), ttl_seconds],
            )
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to update managed Office session") from exc
        return _deserialize_pairs(value)

    async def publish_result(
        self,
        session_id: str,
        *,
        result_sha256: str,
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None:
        try:
            value = await self._publish_result_script(
                keys=[self._key(session_id)],
                args=[result_sha256, _now_ms(), ttl_seconds],
            )
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to publish managed Office result") from exc
        return _deserialize_pairs(value)

    async def count_for_owner(self, owner_user_id: str) -> int:
        count = 0
        try:
            async for key in self._redis.scan_iter(match="managed-office:session:*", count=100):
                if await self._redis.hget(key, "owner_user_id") == owner_user_id:
                    count += 1
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to count managed Office sessions") from exc
        return count

    async def delete(self, session_id: str) -> OfficeSessionRecord | None:
        try:
            value = await self._delete_script(keys=[self._key(session_id)], args=[])
        except Exception as exc:
            raise OfficeSessionStoreUnavailable("Unable to delete managed Office session") from exc
        return _deserialize_pairs(value)


def _serialize(record: OfficeSessionRecord) -> dict[str, str]:
    return {field: str(getattr(record, field)) for field in record.__dataclass_fields__}


def _deserialize(value: dict[str, str] | None) -> OfficeSessionRecord | None:
    if not value:
        return None
    try:
        return OfficeSessionRecord(
            session_id=value["session_id"],
            owner_user_id=value["owner_user_id"],
            document_key=value["document_key"],
            title=value["title"],
            extension=value["extension"],
            source_key=value["source_key"],
            result_key=value["result_key"],
            status=value["status"],
            result_revision=int(value["result_revision"]),
            result_sha256=value.get("result_sha256", ""),
            message=value.get("message", ""),
            created_at_ms=int(value["created_at_ms"]),
            updated_at_ms=int(value["updated_at_ms"]),
            expires_at_ms=int(value["expires_at_ms"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficeSessionStoreUnavailable("Managed Office session state is corrupt") from exc


def _deserialize_pairs(value: list[str] | None) -> OfficeSessionRecord | None:
    if not value:
        return None
    return _deserialize(dict(zip(value[::2], value[1::2], strict=True)))


def _now_ms() -> int:
    return int(time.time() * 1000)

