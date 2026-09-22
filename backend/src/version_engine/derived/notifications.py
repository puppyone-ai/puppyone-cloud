"""Server-side WebSocket notification manager for the Write Engine.

Implements the producer side of PuppyOne version notifications.
After every successful push the hook in
``src.version_engine.derived.hooks.run_post_push_hook`` calls
:meth:`NotificationManager.broadcast_commit_update` to fan out a
``commit_update`` JSON frame to every WebSocket client subscribed to
the affected scope.

Persistence
-----------
This server-side manager only keeps a small per-client queue for clients
that disconnect mid-frame; the queue is flushed when the client reconnects.
Pre-existing missed events are reconciled by normal version refresh/fetch.

Concurrency
-----------
The manager is process-wide singleton-style. Connections register via
:meth:`register` and unregister on disconnect. Redis distributes business
events to every replica; each replica keeps only its live socket handles.
Canonical history paging is the durable reconnect source when Pub/Sub delivery
is interrupted.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket

from src.utils.logger import log_debug, log_info, log_warning

# Redis channel for cross-replica commit_update fan-out (ISSUE-015).
_PUBSUB_CHANNEL = "puppyone:version_notifications"


@dataclass
class _ClientConn:
    websocket: WebSocket
    project_id: str
    scope_path: str  # normalised, no leading/trailing /
    agent: str
    client_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class NotificationManager:
    """Process-wide WebSocket notification manager.

    Use :meth:`get` for the singleton; the FastAPI app initialises one
    on startup and tears it down on shutdown.
    """

    # Bound size of the per-client offline queue — a runaway producer
    # shouldn't grow memory unbounded for a slow consumer. 500 events
    # is enough to hold ~30 minutes of busy work; clients that fall
    # further behind are expected to reconcile via version refresh/fetch.
    MAX_OFFLINE_PER_CLIENT = 500

    _instance: "NotificationManager | None" = None

    def __init__(self, *, redis_client=None):
        # (project_id, scope_path) → list of active connections.
        self._conns: dict[tuple[str, str], list[_ClientConn]] = defaultdict(list)
        # client_id → list of pending events for offline clients.
        self._offline: dict[str, list[dict]] = defaultdict(list)
        # Single asyncio.Lock guarding both maps. Coarse but writes are
        # cheap — connect/disconnect/broadcast — and this serialises
        # against the iteration in broadcast which would otherwise
        # race a concurrent unregister.
        self._lock = asyncio.Lock()
        # Cross-replica pub/sub (ISSUE-015). Disabled unless NOTIFICATIONS_REDIS_URL
        # is set; origin id lets a replica ignore the events it published itself.
        self._origin_id = uuid.uuid4().hex
        self._redis = redis_client
        self._injected_redis = redis_client is not None
        self._pubsub_task: asyncio.Task | None = None
        self._pubsub_started = False

    @classmethod
    def get(cls) -> "NotificationManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls):
        cls._instance = None

    # ── connection lifecycle ───────────────────────────

    async def register(
        self, websocket: WebSocket, project_id: str,
        scope_path: str, agent: str,
    ) -> _ClientConn:
        await self._ensure_pubsub_started()
        scope_norm = (scope_path or "").strip("/")
        conn = _ClientConn(
            websocket=websocket, project_id=project_id,
            scope_path=scope_norm, agent=agent,
        )
        async with self._lock:
            self._conns[(project_id, scope_norm)].append(conn)
        # Per-client lifecycle is debug-only — there can be many of
        # these per session. Broadcast events stay at info so
        # ``commit_update`` fan-out is still visible at default level.
        log_debug(
            f"[NotificationManager] registered client_id={conn.client_id} "
            f"project={project_id} scope={scope_norm!r} agent={agent}"
        )
        return conn

    async def unregister(self, conn: _ClientConn):
        async with self._lock:
            bucket = self._conns.get((conn.project_id, conn.scope_path))
            if bucket is not None:
                try:
                    bucket.remove(conn)
                except ValueError:
                    pass
                if not bucket:
                    self._conns.pop((conn.project_id, conn.scope_path), None)
        log_debug(
            f"[NotificationManager] unregistered client_id={conn.client_id}"
        )

    # ── broadcast ──────────────────────────────────────

    async def broadcast_commit_update(
        self, project_id: str, scope_path: str, *,
        commit_id: str, pushed_by: str, changes: list[dict],
        message: str = "", scope_hash: str = "",
        pusher_client_id: str = "",
    ):
        """Send a ``commit_update`` frame to every client subscribed to
        the affected scope (or any ancestor scope, since pushing into
        ``docs/sub`` is also visible to a listener on ``docs``).

        ``pusher_client_id`` — when set, ONLY that connection is
        suppressed (the device/tab that fired the write doesn't need
        to echo to itself). When empty (e.g. publish came from Git
        push or a CLI client that doesn't hold a WS), we fall back to
        agent-level suppression, which has the known limitation that
        two devices sharing the same agent identity won't echo to
        either. Pass the client_id from L1 routers that originated the
        write to get clean per-tab echo behavior.
        """
        scope_norm = (scope_path or "").strip("/")
        payload = {
            "type": "commit_update",
            "notification_id": commit_id,
            "scope": scope_norm,
            "commit_id": commit_id,
            "pushed_by": pushed_by,
            "message": message,
            "scope_hash": scope_hash,
            "changed_files": [c.get("path", "") for c in (changes or [])],
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        await self._ensure_pubsub_started()
        # Fan out to this replica's own connections...
        await self._local_broadcast(
            project_id, scope_norm, payload,
            pushed_by=pushed_by, pusher_client_id=pusher_client_id,
        )
        # ...and to other replicas via Redis (no-op when pub/sub is disabled).
        await self._publish({
            "origin": self._origin_id,
            "project_id": project_id,
            "scope_norm": scope_norm,
            "payload": payload,
            "pushed_by": pushed_by,
            "pusher_client_id": pusher_client_id,
        })

    async def _local_broadcast(
        self, project_id: str, scope_norm: str, payload: dict, *,
        pushed_by: str, pusher_client_id: str,
    ) -> None:
        """Fan a prepared commit_update payload out to THIS replica's clients."""
        # Targets: every connection on the same project whose scope
        # contains the affected path. ``''`` (Project root) is the
        # ancestor of everything.
        targets: list[_ClientConn] = []
        async with self._lock:
            for (proj, conn_scope), bucket in self._conns.items():
                if proj != project_id:
                    continue
                if conn_scope == "" or _is_ancestor(conn_scope, scope_norm):
                    targets.extend(bucket)

        sent = 0
        dropped = 0
        for conn in targets:
            if _is_echo(conn, pushed_by, pusher_client_id):
                continue
            try:
                await conn.websocket.send_json(payload)
                sent += 1
            except Exception as e:
                log_warning(
                    f"[NotificationManager] send failed to "
                    f"client_id={conn.client_id}: {e} — queueing offline"
                )
                self._enqueue_offline(conn.client_id, payload)
                dropped += 1

        if sent or dropped:
            log_info(
                f"[NotificationManager] broadcast commit_update "
                f"project={project_id} scope={scope_norm!r} "
                f"sent={sent} dropped={dropped}"
            )

    # ── cross-replica pub/sub (ISSUE-015) ──────────────

    async def _ensure_pubsub_started(self) -> None:
        """Lazily connect Redis + start the subscriber. Fail-open: any error
        degrades to process-local fan-out (the pre-ISSUE-015 behaviour)."""
        if self._pubsub_started:
            return
        self._pubsub_started = True  # only attempt once
        if self._injected_redis:
            self._pubsub_task = asyncio.create_task(self._subscriber_loop())
            return
        from src.config import settings
        url = getattr(settings, "NOTIFICATIONS_REDIS_URL", "") or ""
        if not url:
            return
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
            self._pubsub_task = asyncio.create_task(self._subscriber_loop())
            log_info("[NotificationManager] cross-replica pub/sub enabled")
        except Exception as e:  # noqa: BLE001 — never break notifications on redis issues
            log_warning(f"[NotificationManager] pub/sub disabled (init failed): {e}")
            self._redis = None

    async def _publish(self, routing: dict) -> None:
        if not self._redis:
            return
        try:
            await self._redis.publish(_PUBSUB_CHANNEL, json.dumps(routing))
        except Exception as e:  # noqa: BLE001 — local fan-out already happened
            log_warning(f"[NotificationManager] publish failed (local-only): {e}")

    async def _subscriber_loop(self) -> None:
        """Receive commit_updates published by OTHER replicas and fan them out
        to this replica's local connections. Own-origin events are skipped."""
        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(_PUBSUB_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except Exception:  # noqa: BLE001
                    continue
                if data.get("origin") == self._origin_id:
                    continue  # already fanned out locally by this replica
                await self._local_broadcast(
                    data.get("project_id", ""), data.get("scope_norm", ""),
                    data.get("payload", {}),
                    pushed_by=data.get("pushed_by", ""),
                    pusher_client_id=data.get("pusher_client_id", ""),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log_warning(f"[NotificationManager] subscriber loop ended: {e}")

    async def stop_pubsub(self) -> None:
        """Cancel the subscriber + close Redis (call on app shutdown)."""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pubsub_task
            self._pubsub_task = None
        if self._redis:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    def _enqueue_offline(self, client_id: str, payload: dict):
        q = self._offline[client_id]
        if len(q) >= self.MAX_OFFLINE_PER_CLIENT:
            q.pop(0)
        q.append(payload)

    async def flush_offline(self, conn: _ClientConn):
        """Drain queued events for a freshly-reconnected client."""
        pending = self._offline.pop(conn.client_id, [])
        delivered = 0
        for payload in pending:
            try:
                await conn.websocket.send_json(payload)
                delivered += 1
            except Exception as e:  # noqa: BLE001 — drop noisy disconnects
                log_warning(f"[NotificationManager] flush failed: {e}")
                # Re-queue the rest in case the client reconnects again.
                self._offline[conn.client_id].extend(
                    pending[pending.index(payload):]
                )
                break
        if delivered:
            log_info(
                f"[NotificationManager] flushed {delivered} offline "
                f"event(s) to client_id={conn.client_id}"
            )


def _is_ancestor(maybe_ancestor: str, descendant: str) -> bool:
    """Return True if *maybe_ancestor* contains *descendant* as a path
    prefix. Empty string is the Project root and an ancestor of everything.
    """
    if not maybe_ancestor:
        return True
    if maybe_ancestor == descendant:
        return True
    return descendant.startswith(maybe_ancestor + "/")


def _is_echo(conn, pushed_by: str, pusher_client_id: str) -> bool:
    """Return True if ``conn`` is the source connection of the push.

    Precedence:
      1. If we know the pusher's specific ``client_id``, only that exact
         WS connection is suppressed. Other tabs/devices of the same
         user still receive their own commit_update.
      2. Otherwise fall back to agent-level suppression (legacy path) so
         a single-device user still doesn't see a redundant echo. The
         drawback — two devices sharing the same agent both go silent —
         is documented at the broadcast call site.
    """
    if pusher_client_id:
        return conn.client_id == pusher_client_id
    return conn.agent == pushed_by
