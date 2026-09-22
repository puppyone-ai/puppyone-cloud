"""Hermetic tests for ISSUE-003 scope hashing and ISSUE-015 notifications.

No Supabase, no Redis, no network — fake clients / websockets only.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

# ── ISSUE-003: scope access-key hash lookup (gated dual-read) ───────────────

class _FakeScopeClient:
    """Chainable fake: returns a row only when an .eq() on a "hit" column runs."""

    def __init__(self, hit_columns):
        self.hit_columns = set(hit_columns)
        self.eq_calls: list[tuple] = []
        self._last_eq_col = None
        self.is_calls: list[tuple] = []
        self.table_name = ""

    @property
    def client(self):
        return self

    def table(self, name):
        self.table_name = name
        return self

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        self._last_eq_col = col
        return self

    def limit(self, _n):
        return self

    def is_(self, *a, **k):
        self.is_calls.append(tuple(a))
        return self

    def execute(self):
        if self.table_name == "access_surface_credentials":
            data = ([{
                "id": "credential-1", "org_id": "org-1", "project_id": "p1",
                "access_surface_id": "surface-1", "key_hash": "hashed",
                "credential_type": "bearer_token", "status": "active",
            }] if "key_hash" in self.hit_columns else [])
        elif self.table_name == "access_surfaces":
            data = [{
                "id": "surface-1", "org_id": "org-1",
                "scope_id": "s1", "project_id": "p1",
                "kind": "cli", "status": "active",
            }]
        elif self.table_name == "repository_scopes":
            data = [{
                "id": "s1", "project_id": "p1", "path": "docs",
                "exclude": [], "max_mode": "rw",
            }]
        else:
            data = []
        return SimpleNamespace(data=data)


def _find():
    from src.version_engine.infrastructure.supabase import scope_repository
    return scope_repository.resolve_scope_access_credential


def test_scope_auth_always_resolves_by_hash_first(monkeypatch):
    from src.config import settings
    from src.repo.access_credentials import access_token_hash
    monkeypatch.setattr(
        settings,
        "ACCESS_CREDENTIAL_HASH_SECRET",
        "test-credential-secret-at-least-32-characters",
    )
    client = _FakeScopeClient(hit_columns={"key_hash"})
    row = _find()(client, "cli_secretkey")
    assert row is not None
    # First lookup is by hash of the key, not the plaintext.
    assert ("key_hash", access_token_hash("cli_secretkey")) in client.eq_calls


def test_scope_plaintext_fallback_is_fully_retired(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(
        settings,
        "ACCESS_CREDENTIAL_HASH_SECRET",
        "test-credential-secret-at-least-32-characters",
    )
    # Hash lookup misses; plaintext storage has been removed and is never read.
    client = _FakeScopeClient(hit_columns={"access_key"})
    row = _find()(client, "cli_secretkey")
    assert row is None
    cols = [c for c, _ in client.eq_calls]
    assert "key_hash" in cols
    assert "access_key" not in cols


# ── ISSUE-015: cluster-aware notifications (gated, fail-open) ────────────────

class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple] = []

    async def publish(self, channel, data):
        self.published.append((channel, data))


class _SharedPubSubBus:
    def __init__(self):
        self.queues = []

    def client(self):
        return _BusClient(self)


class _BusPubSub:
    def __init__(self, bus):
        self.bus = bus
        self.queue = asyncio.Queue()

    async def subscribe(self, _channel):
        self.bus.queues.append(self.queue)

    async def listen(self):
        while True:
            yield await self.queue.get()


class _BusClient:
    def __init__(self, bus):
        self.bus = bus

    def pubsub(self):
        return _BusPubSub(self.bus)

    async def publish(self, _channel, data):
        for queue in list(self.bus.queues):
            await queue.put({"type": "message", "data": data})

    async def aclose(self):
        return None


def _manager():
    from src.version_engine.derived.notifications import NotificationManager
    return NotificationManager()


def test_local_fanout_delivers_to_subscribed_client():
    async def main():
        m = _manager()
        ws = _FakeWS()
        await m.register(ws, "p1", "docs", agent="agent-a")
        await m.broadcast_commit_update(
            "p1", "docs", commit_id="c1", pushed_by="someone-else", changes=[],
        )
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "commit_update"
        assert ws.sent[0]["commit_id"] == "c1"
    asyncio.run(main())


def test_pubsub_disabled_by_default():
    async def main():
        m = _manager()
        await m._ensure_pubsub_started()
        assert m._redis is None and m._pubsub_task is None
    asyncio.run(main())


def test_broadcast_publishes_with_origin_when_redis_present():
    async def main():
        import json
        m = _manager()
        m._pubsub_started = True          # skip real connect
        m._redis = _FakeRedis()
        await m.broadcast_commit_update(
            "p1", "docs/sub", commit_id="c9", pushed_by="u", changes=[{"path": "a.md"}],
        )
        assert len(m._redis.published) == 1
        _channel, raw = m._redis.published[0]
        msg = json.loads(raw)
        assert msg["origin"] == m._origin_id      # loop-prevention tag
        assert msg["project_id"] == "p1"
        assert msg["scope_norm"] == "docs/sub"
        assert msg["payload"]["commit_id"] == "c9"
    asyncio.run(main())


def test_two_managers_fan_out_across_shared_bus_once():
    async def main():
        from src.version_engine.derived.notifications import NotificationManager

        bus = _SharedPubSubBus()
        first = NotificationManager(redis_client=bus.client())
        second = NotificationManager(redis_client=bus.client())
        ws = _FakeWS()
        await first.register(ws, "p1", "docs", agent="listener")
        await second._ensure_pubsub_started()
        await asyncio.sleep(0)

        await second.broadcast_commit_update(
            "p1",
            "docs/sub",
            commit_id="cross-instance-1",
            pushed_by="writer",
            changes=[{"path": "docs/a.md"}],
        )
        for _ in range(10):
            if ws.sent:
                break
            await asyncio.sleep(0)

        assert [event["commit_id"] for event in ws.sent] == ["cross-instance-1"]
        await first.stop_pubsub()
        await second.stop_pubsub()

    asyncio.run(main())
