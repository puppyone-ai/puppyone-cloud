"""
MCP session 注册表
用于记录 api_key -> session，并在工具列表变更时通知客户端刷新
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
import weakref

import anyio
from mcp.server.session import ServerSession

logger = logging.getLogger(__name__)


class SessionRegistry:
    """Session 注册表：跟踪活跃 ServerSession，用于通知工具变更"""

    CHANNEL = "puppyone:mcp_tools_changed"

    def __init__(self, redis_client=None) -> None:
        self._lock = anyio.Lock()
        self._by_api_key_hash: dict[str, weakref.WeakSet[ServerSession]] = {}
        self._by_surface: dict[str, weakref.WeakSet[ServerSession]] = {}
        self._redis = redis_client
        self._origin = uuid.uuid4().hex
        self._subscriber_task: asyncio.Task | None = None
        self._closing = False

    @staticmethod
    def _credential_route(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    async def bind(self, api_key: str, session: ServerSession) -> None:
        """绑定 api_key 和 session"""
        async with self._lock:
            route = self._credential_route(api_key)
            bucket = self._by_api_key_hash.get(route)
            if bucket is None:
                bucket = weakref.WeakSet()
                self._by_api_key_hash[route] = bucket
            bucket.add(session)

    async def bind_surface(self, access_surface_id: str, session: ServerSession) -> None:
        async with self._lock:
            bucket = self._by_surface.get(access_surface_id)
            if bucket is None:
                bucket = weakref.WeakSet()
                self._by_surface[access_surface_id] = bucket
            bucket.add(session)

    async def notify_tools_list_changed(self, api_key: str) -> int:
        """通知指定 api_key 的所有 session：工具列表已变更"""
        return await self._notify_credential_route(self._credential_route(api_key))

    async def _notify_credential_route(self, route: str) -> int:
        async with self._lock:
            bucket = self._by_api_key_hash.get(route)
            sessions = list(bucket) if bucket is not None else []

        sent = 0
        for s in sessions:
            try:
                await s.send_tool_list_changed()
                sent += 1
            except Exception:
                continue

        return sent

    async def broadcast_tools_list_changed(self, api_key: str) -> int:
        route = self._credential_route(api_key)
        sent = await self._notify_credential_route(route)
        await self._publish({"kind": "credential", "route": route})
        return sent

    async def notify_surface_changed(self, access_surface_id: str) -> int:
        async with self._lock:
            bucket = self._by_surface.get(access_surface_id)
            sessions = list(bucket) if bucket is not None else []

        sent = 0
        for session in sessions:
            try:
                await session.send_tool_list_changed()
                sent += 1
            except Exception:
                continue
        return sent

    async def broadcast_surface_changed(self, access_surface_id: str) -> int:
        sent = await self.notify_surface_changed(access_surface_id)
        await self._publish({"kind": "surface", "route": access_surface_id})
        return sent

    async def start(self, redis_url: str | None) -> None:
        self._closing = False
        if self._redis is None and redis_url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                redis_url, encoding="utf-8", decode_responses=True
            )
        if self._redis is not None and self._subscriber_task is None:
            self._subscriber_task = asyncio.create_task(self._subscriber_loop())

    async def _publish(self, payload: dict) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.publish(
                self.CHANNEL,
                json.dumps({"origin": self._origin, **payload}),
            )
        except Exception as exc:  # local sessions were already notified
            logger.warning("MCP notification publish failed: %s", exc)

    async def _subscriber_loop(self) -> None:
        while not self._closing:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(self.CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if payload.get("origin") == self._origin:
                        continue
                    route = str(payload.get("route") or "")
                    if payload.get("kind") == "credential":
                        await self._notify_credential_route(route)
                    elif payload.get("kind") == "surface":
                        await self.notify_surface_changed(route)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MCP notification subscriber reconnecting: %s", exc)
                await asyncio.sleep(1)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe(self.CHANNEL)
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def close(self) -> None:
        self._closing = True
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._subscriber_task
            self._subscriber_task = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
