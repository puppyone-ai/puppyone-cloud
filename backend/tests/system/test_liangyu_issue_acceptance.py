"""Cross-module acceptance cases for the owner-liangyu remediations.

These are intentionally not isolated units: they exercise worker policy plus a
real ObjectStore repository and two independent MCP transport registries over
one pub/sub bus. Ephemeral sandbox recovery is covered by the sandbox reaper.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mcp_service.core.session_registry import SessionRegistry
from src.version_engine.derived.object_gc_worker import process_object_gc_projects
from src.version_engine.storage.object_store import ObjectStore


class _GcQuery:
    def __init__(self, db, table: str):
        self.db = db
        self.table = table
        self.filters = []
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append(lambda row, k=key, v=value: row.get(k) == v)
        return self

    def gte(self, key, value):
        self.filters.append(lambda row, k=key, v=value: str(row.get(k, "")) >= str(v))
        return self

    def insert(self, payload):
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.payload is not None:
            self.db.rows.setdefault(self.table, []).append(self.payload)
            return SimpleNamespace(data=[self.payload])
        rows = self.db.rows.get(self.table, [])
        return SimpleNamespace(data=[r for r in rows if all(f(r) for f in self.filters)])


class _GcDb:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _GcQuery(self, name)


def test_issue_012_production_gc_evidence_is_enforced_per_project(tmp_path, monkeypatch):
    """Another tenant's clean history must not unlock destructive GC."""

    today = datetime.now(UTC).date()
    evidence = [
        {
            "project_id": "ready-project",
            "dry_run": True,
            "created_at": datetime.combine(
                today - timedelta(days=offset), datetime.min.time(), UTC
            ).isoformat(),
            "errors": [],
            "sweep_skipped_for_safety": False,
        }
        for offset in range(2)
    ]
    db = _GcDb({"version_object_gc_runs": evidence})
    repos = {
        project_id: SimpleNamespace(
            _project_id=project_id,
            store=ObjectStore(tmp_path / project_id),
            history=SimpleNamespace(list_object_gc_roots=lambda: []),
        )
        for project_id in ("ready-project", "unproven-project")
    }
    manager = SimpleNamespace(get_gc_repo=lambda project_id: repos[project_id])
    monkeypatch.setattr(
        "src.version_engine.derived.object_gc_worker.settings.APP_ENV", "production"
    )
    monkeypatch.setattr(
        "src.version_engine.derived.object_gc_worker.settings.VERSION_OBJECT_GC_REQUIRED_DRY_RUN_DAYS",
        2,
    )

    results = process_object_gc_projects(
        repo_manager=manager,
        client=db,
        project_ids=["ready-project", "unproven-project"],
        dry_run=False,
        retention_seconds=0,
    )

    by_project = {result.project_id: result for result in results}
    assert by_project["ready-project"].dry_run is False
    assert by_project["unproven-project"].dry_run is True


class _PubSub:
    def __init__(self, bus):
        self.bus = bus
        self.queue = asyncio.Queue()

    async def subscribe(self, channel):
        self.bus.subscribers.setdefault(channel, []).append(self.queue)

    async def listen(self):
        while True:
            yield await self.queue.get()

    async def unsubscribe(self, channel):
        self.bus.subscribers.get(channel, []).remove(self.queue)

    async def aclose(self):
        return None


class _RedisBus:
    def __init__(self):
        self.subscribers = {}

    def client(self):
        bus = self

        class Client:
            def pubsub(self):
                return _PubSub(bus)

            async def publish(self, channel, payload):
                for queue in list(bus.subscribers.get(channel, [])):
                    await queue.put({"type": "message", "data": payload})

            async def aclose(self):
                return None

        return Client()


class _LiveMcpSession:
    def __init__(self):
        self.notifications = 0

    async def send_tool_list_changed(self):
        self.notifications += 1


@pytest.mark.asyncio
async def test_issue_015_mcp_tool_change_crosses_replica_boundary():
    bus = _RedisBus()
    replica_a = SessionRegistry(bus.client())
    replica_b = SessionRegistry(bus.client())
    session = _LiveMcpSession()
    await replica_a.start(None)
    await replica_b.start(None)
    await asyncio.sleep(0)
    await replica_b.bind("mcp_super_secret", session)

    await replica_a.broadcast_tools_list_changed("mcp_super_secret")
    for _ in range(20):
        if session.notifications:
            break
        await asyncio.sleep(0.01)

    assert session.notifications == 1
    # Routing payloads use a digest, never the bearer token itself.
    await replica_a.close()
    await replica_b.close()
