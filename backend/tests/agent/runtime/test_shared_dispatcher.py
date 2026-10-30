"""One dispatcher and concurrency budget; every Run borrows its workbench."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime import worker


@pytest.mark.asyncio
async def test_the_dispatcher_claims_only_workbench_runs(monkeypatch):
    from src.config import settings

    session = [{"id": "session"}]
    shared = [
        {"id": "second", "workbench_id": "workbench"},
        {"id": "first", "workbench_id": "workbench"},
    ]
    claims, active = [], []
    ready, release = asyncio.Event(), asyncio.Event()

    def claim(kind, queue):
        claims.append(kind)
        return queue.pop() if queue else None

    repo = SimpleNamespace(
        client=object(),
        claim_run=lambda **_: claim("session", session),
        claim_workbench_run=lambda **_: claim("shared", shared),
    )
    owner = SimpleNamespace(
        reap=AsyncMock(), lifecycle=SimpleNamespace(serve=lambda **_: release.wait())
    )

    class Supervisor:
        def __init__(self, *args, **kwargs):
            self.options = kwargs

        async def run_claim(self, run):
            active.append((run["id"], self.options))
            if len(active) == 2:
                ready.set()
            await release.wait()

    monkeypatch.setattr(settings, "CLOUD_AGENT_CONCURRENCY", 2)
    monkeypatch.setattr(worker, "RunRepository", lambda: repo)
    monkeypatch.setattr(worker, "Admission", lambda _: object())
    monkeypatch.setattr(worker, "Publication", lambda transport, filesystem: object())
    monkeypatch.setattr(worker, "RunGitTransport", lambda _: object())
    monkeypatch.setattr(
        worker,
        "build_worker_version_engine_container",
        lambda **_: SimpleNamespace(repo_manager=None),
    )
    monkeypatch.setattr(worker, "get_billing_gateway", lambda: None)
    monkeypatch.setattr(worker, "get_provider_registry", lambda: None)
    monkeypatch.setattr(worker, "get_inference_service", lambda *_: None)
    monkeypatch.setattr(worker, "get_runtime_metering_service", lambda: None)
    monkeypatch.setattr(worker, "RunSupervisor", Supervisor)
    monkeypatch.setattr(worker, "reap_workspaces", AsyncMock())
    dispatch = asyncio.create_task(worker.serve(workbench_owner=owner))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        options = dict(active)
        assert set(options) == {"first", "second"}
        assert options["first"]["shared_tasks"].owner is owner
        assert "workspace" not in options["first"]
        assert "session" not in claims
        assert session == [{"id": "session"}]
    finally:
        dispatch.cancel()
        with suppress(asyncio.CancelledError):
            await dispatch
