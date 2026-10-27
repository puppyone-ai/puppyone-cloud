"""A new default must not strand old workspace intents, tasks or maintenance."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import Settings, settings
from src.platform.sandbox import (
    WorkspaceUnavailable,
    bindings,
    require_binding,
    workspace_providers,
)
from tests.agent.runtime.test_workspace_readiness import lifecycle


@pytest.fixture
def cutover(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "sprites")
    monkeypatch.setattr(settings, "CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS", "fly")
    owner, row, sprite = lifecycle()
    sprite.name = "sprites"
    sprite.artifact = Mock(return_value="new-worker")
    fly = SimpleNamespace(
        name="fly",
        capabilities=sprite.capabilities,
        validate=Mock(),
        preflight=AsyncMock(),
        aclose=AsyncMock(),
    )
    owner.providers = Mock(
        side_effect=lambda name: {None: sprite, "sprites": sprite, "fly": fly}[name]
    )
    row.update(
        binding={"provider": "fly", "artifact": "old-worker"},
        resource={"provider": "fly", "resource_id": "old-machine"},
    )
    owner.repository.request_open.side_effect = lambda row, *_: row
    return owner, row, sprite, fly


def test_sprites_is_the_source_default_and_fly_is_retained_for_existing_bindings():
    assert Settings.model_fields["CLOUD_AGENT_SANDBOX_PROVIDER"].default == "sprites"
    assert Settings.model_fields["CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS"].default == "fly"


@pytest.mark.asyncio
async def test_existing_fly_reopen_does_not_use_new_default_credentials(cutover):
    owner, row, sprite, fly = cutover
    sprite.validate.side_effect = ValueError("new provider not configured yet")
    await owner.request("user", "project", row)
    assert owner.repository.request_open.call_args.args[1] == row["binding"]
    fly.validate.assert_called_once_with("old-worker", row["resource"])
    sprite.validate.assert_not_called()
    owner.channel_factory.assert_not_called()


@pytest.mark.asyncio
async def test_retired_workspace_selects_sprites_for_the_next_generation(cutover):
    owner, row, sprite, fly = cutover
    row.update(state="retired", resource=None)
    await owner.request("user", "project", row)
    assert owner.repository.request_open.call_args.args[1] == {
        "provider": "sprites",
        "artifact": "new-worker",
    }
    sprite.validate.assert_called_once()
    fly.validate.assert_not_called()


@pytest.mark.asyncio
async def test_removing_retained_provider_fails_closed_without_rebinding(cutover, monkeypatch):
    owner, row, _, fly = cutover
    monkeypatch.setattr(settings, "CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS", "")
    with pytest.raises(WorkspaceUnavailable, match="not_configured"):
        await owner.request("user", "project", row)
    assert row["binding"]["provider"] == "fly"
    owner.repository.request_open.assert_not_called()
    fly.validate.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_cache_is_separate_for_each_provider_and_artifact(cutover):
    owner, row, sprite, fly = cutover
    row["resource"] = None
    await owner.preflight(row)
    await owner.preflight(row)
    await owner.preflight({**row, "binding": {"provider": "sprites", "artifact": "new-worker"}})
    await owner.preflight({**row, "binding": {"provider": "sprites", "artifact": "next-worker"}})
    fly.preflight.assert_awaited_once_with("old-worker")
    assert sprite.preflight.await_count == 2


@pytest.mark.asyncio
async def test_worker_drains_old_and_new_pending_queues(cutover):
    owner, old, *_ = cutover
    new = {**old, "id": "new", "binding": {"provider": "sprites", "artifact": "new-worker"}}
    stop, executed = asyncio.Event(), []
    owner.repository.pending.side_effect = lambda name, pool: [old] if name == "fly" else [new]

    async def execute(row):
        executed.append(row["binding"]["provider"])
        if len(executed) == 2:
            stop.set()

    owner.execute = execute
    async with asyncio.timeout(3):
        await owner.serve(stop=stop, grace=1)
    assert sorted(executed) == ["fly", "sprites"]
    assert {call.args[0] for call in owner.repository.pending.call_args_list} == {"fly", "sprites"}


@pytest.mark.asyncio
async def test_one_provider_failure_does_not_skip_other_provider_maintenance(cutover):
    owner, old, _, fly = cutover
    old["state"] = "pausing"
    new = {**old, "id": "new", "binding": {"provider": "sprites", "artifact": "new-worker"}}
    owner.repository.due.side_effect = lambda name, pool: [old] if name == "fly" else [new]
    owner.repository.lifecycle_claim.side_effect = lambda row: {"acquired": True, "workbench": row}
    owner.repository.get.return_value = new

    async def pause(row):
        if row["binding"]["provider"] == "sprites":
            raise RuntimeError("provider unavailable")

    owner.pause = AsyncMock(side_effect=pause)
    await owner.reap()
    assert [call.args[0]["binding"]["provider"] for call in owner.pause.call_args_list] == [
        "sprites",
        "fly",
    ]
    fly.validate.assert_called_once()


@pytest.mark.asyncio
async def test_agent_capability_accepts_a_retained_workspace_provider(cutover, monkeypatch):
    _, row, _, fly = cutover
    monkeypatch.setattr(bindings, "runtime_provider", lambda name: fly)
    await require_binding(row["binding"], row["resource"], workspace_only=True)
    fly.validate.assert_called_once_with("old-worker", row["resource"])


def test_hosted_cutover_cannot_enable_local_docker(cutover, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS", "docker")
    with pytest.raises(ValueError, match="Docker"):
        workspace_providers()
