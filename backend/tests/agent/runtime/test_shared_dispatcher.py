"""One dispatcher/concurrency budget; shared claims require an explicit owner."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime import worker


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_legacy_and_shared_claims_use_the_same_dispatcher(monkeypatch, enabled):
    from src.config import settings

    legacy, shared = [{"id": "legacy"}], [{"id": "shared", "workbench_id": "workbench"}]
    claims, active = [], []
    ready, release = asyncio.Event(), asyncio.Event()

    def claim(kind, queue):
        claims.append(kind)
        return queue.pop() if queue else None

    repo = SimpleNamespace(
        client=object(),
        claim_run=lambda **_: claim("legacy", legacy),
        claim_workbench_run=lambda **_: claim("shared", shared),
    )
    owner = (
        SimpleNamespace(reap=AsyncMock(), lifecycle=SimpleNamespace(serve=release.wait))
        if enabled
        else None
    )

    class Supervisor:
        def __init__(self, *args, **kwargs):
            self.options = kwargs

        async def run_claim(self, run):
            active.append((run["id"], self.options))
            if len(active) == (2 if enabled else 1):
                ready.set()
            await release.wait()

    monkeypatch.setattr(settings, "CLOUD_AGENT_CONCURRENCY", 2 if enabled else 1)
    monkeypatch.setattr(worker, "RunRepository", lambda: repo)
    monkeypatch.setattr(worker, "Admission", lambda _: object())
    monkeypatch.setattr(worker, "Publication", lambda transport, filesystem: object())
    monkeypatch.setattr(worker, "RunGitTransport", lambda _: object())
    monkeypatch.setattr(worker, "build_bound_tools", lambda _admission, _engine: object())
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
        assert len(active) == (2 if enabled else 1)
        options = dict(active)
        assert options["legacy"]["workspace"] is not None
        assert options["legacy"]["shared_tasks"] is None
        if enabled:
            assert options["shared"]["workspace"] is None
            assert options["shared"]["shared_tasks"].owner is owner
            assert claims[:2] == ["legacy", "shared"]
        else:
            assert "shared" not in claims
    finally:
        dispatch.cancel()
        with suppress(asyncio.CancelledError):
            await dispatch
