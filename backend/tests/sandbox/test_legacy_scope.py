"""Leftover Scope Sandbox machines are destroyed and their rows marked destroyed."""

import asyncio

from src.platform.sandbox import legacy_scope, reaper


class _Sessions:
    def __init__(self, rows):
        self.rows = {row["scope_id"]: {**row, "state": "running"} for row in rows}

    def live(self):
        return [
            {key: row[key] for key in ("scope_id", "provider", "sandbox_id")}
            for row in self.rows.values()
            if row["state"] != "destroyed"
        ]

    def mark_destroyed(self, scope_id, now):
        self.rows[scope_id].update(state="destroyed", last_state_change_at=now)


def _resources(monkeypatch, *, absent=True, fail=()):
    calls = []

    async def destroy(provider, resource_id, *, pool):
        calls.append(("destroy", provider, resource_id, pool))
        if resource_id in fail:
            raise RuntimeError("provider unavailable")

    async def is_absent(provider, resource_id, *, pool):
        calls.append(("absent", provider, resource_id, pool))
        return absent

    monkeypatch.setattr(legacy_scope, "destroy_resource", destroy)
    monkeypatch.setattr(legacy_scope, "resource_absent", is_absent)
    return calls


async def test_live_rows_are_destroyed_in_the_legacy_pool_and_marked(monkeypatch):
    sessions = _Sessions([
        {"scope_id": "s-1", "provider": "e2b", "sandbox_id": "sbx-1"},
        {"scope_id": "s-2", "provider": "fly", "sandbox_id": "m-1"},
    ])
    calls = _resources(monkeypatch)

    assert await legacy_scope.retire_legacy_scope_sandboxes_once(sessions, now=50.0) == 2
    assert ("destroy", "e2b", "sbx-1", "legacy_scope") in calls
    assert ("destroy", "fly", "m-1", "legacy_scope") in calls
    assert {row["state"] for row in sessions.rows.values()} == {"destroyed"}
    assert sessions.rows["s-1"]["last_state_change_at"] == 50.0

    calls.clear()
    assert await legacy_scope.retire_legacy_scope_sandboxes_once(sessions) == 0
    assert calls == []


async def test_failed_or_unverified_destruction_keeps_the_row_live(monkeypatch):
    sessions = _Sessions([
        {"scope_id": "s-1", "provider": "e2b", "sandbox_id": "sbx-fail"},
        {"scope_id": "s-2", "provider": "e2b", "sandbox_id": "sbx-2"},
    ])
    _resources(monkeypatch, absent=False, fail={"sbx-fail"})

    assert await legacy_scope.retire_legacy_scope_sandboxes_once(sessions) == 0
    assert {row["state"] for row in sessions.rows.values()} == {"running"}


async def test_sandbox_module_runs_both_reclamation_passes_even_when_one_fails(monkeypatch):
    from src.config import settings
    from src.platform.workspace.sandbox_worker import SandboxModule

    calls = []
    both = asyncio.Event()

    async def execution():
        calls.append("execution")
        raise RuntimeError("database unavailable")

    async def legacy():
        calls.append("legacy")
        both.set()

    monkeypatch.setattr(reaper, "run_execution_reaper_once", execution)
    monkeypatch.setattr(reaper, "retire_legacy_scope_sandboxes_once", legacy)
    monkeypatch.setattr(settings, "SANDBOX_REAPER_ENABLED", True)
    assert [name for name, _ in reaper.reclamation_passes()] == [
        "ephemeral_execution_reaper",
        "legacy_scope_retirement",
    ]

    module = SandboxModule(None, pool="default")
    stop = asyncio.Event()
    task = asyncio.create_task(module.serve(stop=stop))
    await asyncio.wait_for(both.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert calls == ["execution", "legacy"]
    assert module.failed == {"ephemeral_execution_reaper": 1}
    assert module.completed == {"legacy_scope_retirement": 1}
