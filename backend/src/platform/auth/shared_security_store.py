"""Shared, fail-closed Redis primitives for authentication security state."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Protocol

from src.config import settings


class SecurityStoreUnavailable(RuntimeError):
    """Raised when a required shared security control cannot be reached."""


class AtomicTTLStore(Protocol):
    def hit(self, bucket: str, subject: str, limit: int, window_seconds: int) -> bool: ...
    def put(self, namespace: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...
    def consume(self, namespace: str, key: str) -> dict[str, Any] | None: ...
    def read(self, namespace: str, key: str) -> dict[str, Any] | None: ...
    def transition(
        self,
        namespace: str,
        key: str,
        expected: dict[str, Any],
        *,
        replacement: dict[str, Any] | None = None,
        destination: tuple[str, str, dict[str, Any], int] | None = None,
    ) -> bool: ...


class RateLimiter(Protocol):
    def hit(self, bucket: str, subject: str, limit: int, window_seconds: int) -> bool: ...


_RATE_LUA = (
    "local c = redis.call('INCR', KEYS[1])\n"
    "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end\n"
    "return c"
)

# Compare the complete JSON value, then mutate atomically. Invalid proof checks
# happen before this call; stale concurrent readers cannot consume another flow.
_TRANSITION_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local function equal(a,b)
  if type(a) ~= type(b) then return false end
  if type(a) ~= 'table' then return a == b end
  for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
  for k,_ in pairs(b) do if a[k] == nil then return false end end
  return true
end
if not equal(cjson.decode(raw),cjson.decode(ARGV[1])) then return 0 end
if #KEYS == 2 and redis.call('EXISTS',KEYS[2]) == 1 then return 0 end
if ARGV[2] ~= '' then
  redis.call('SET',KEYS[1],ARGV[2],'KEEPTTL')
else
  redis.call('DEL',KEYS[1])
end
if #KEYS == 2 then redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4]) end
return 1
"""


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
            self._transition_script = self._redis.register_script(_TRANSITION_LUA)
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

    def read(self, namespace: str, key: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(self._key(namespace, key))
            value = json.loads(raw) if raw is not None else None
            if value is not None and not isinstance(value, dict):
                raise ValueError("Invalid authentication state")
            return value
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to read authentication state") from exc

    def transition(
        self,
        namespace: str,
        key: str,
        expected: dict[str, Any],
        *,
        replacement: dict[str, Any] | None = None,
        destination: tuple[str, str, dict[str, Any], int] | None = None,
    ) -> bool:
        keys = [self._key(namespace, key)]
        args = [
            json.dumps(expected),
            json.dumps(replacement) if replacement is not None else "",
            "",
            "1",
        ]
        if destination is not None:
            target_namespace, target_key, value, ttl = destination
            keys.append(self._key(target_namespace, target_key))
            args[2:] = [json.dumps(value), str(ttl)]
        try:
            return bool(self._transition_script(keys=keys, args=args))
        except Exception as exc:
            raise SecurityStoreUnavailable("Unable to transition authentication state") from exc

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
