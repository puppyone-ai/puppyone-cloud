"""Shared, fail-closed Redis primitives for authentication security state."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Protocol

from src.config import settings


class SecurityStoreUnavailable(RuntimeError):
    """Raised when a required shared security control cannot be reached."""


class AtomicTTLStore(Protocol):
    def put(self, namespace: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...
    def consume(self, namespace: str, key: str) -> dict[str, Any] | None: ...


class RateLimiter(Protocol):
    def hit(self, bucket: str, subject: str, limit: int, window_seconds: int) -> bool: ...


_RATE_LUA = (
    "local c = redis.call('INCR', KEYS[1])\n"
    "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end\n"
    "return c"
)


class RedisSecurityStore:
    """Redis implementation; values expire and GETDEL makes consumption atomic."""

    def __init__(self, url: str):
        if not url:
            raise SecurityStoreUnavailable("AUTH_SECURITY_REDIS_URL is not configured")
        try:
            import redis

            self._redis = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
            )
            self._rate_script = self._redis.register_script(_RATE_LUA)
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to initialize authentication Redis") from exc

    @staticmethod
    def _key(namespace: str, key: str) -> str:
        return f"auth-security:{namespace}:{key}"

    def put(self, namespace: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        try:
            self._redis.set(self._key(namespace, key), json.dumps(value), ex=ttl_seconds)
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to persist authentication state") from exc

    def consume(self, namespace: str, key: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.getdel(self._key(namespace, key))
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to consume authentication state") from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SecurityStoreUnavailable("Authentication state is corrupt") from exc
        return value if isinstance(value, dict) else None

    def hit(self, bucket: str, subject: str, limit: int, window_seconds: int) -> bool:
        try:
            count = int(
                self._rate_script(
                    keys=[self._key(f"rate:{bucket}", subject)],
                    args=[window_seconds],
                )
            )
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to apply authentication rate limit") from exc
        return count > limit


@lru_cache(maxsize=1)
def get_auth_security_store() -> RedisSecurityStore:
    return RedisSecurityStore(settings.auth_security_redis_url)
