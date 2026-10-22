"""Pinned SDK attachment contract: GET credentials, never implicit resume."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from e2b import AsyncSandbox
from e2b.api.client.api.sandboxes import get_sandboxes_sandbox_id
from e2b.api.client.models import SandboxDetail, SandboxLifecycle, SandboxOnTimeout, SandboxState

from src.config import settings
from src.platform.workspace.execution.providers.e2b import E2BRuntimeProvider
from src.platform.workspace.execution.worker_port import WorkerDisconnected

pytestmark = pytest.mark.asyncio


@pytest.fixture
def attachment(monkeypatch):
    monkeypatch.setattr(settings, "E2B_API_KEY", "fixture-only")
    for name in ("connect", "create", "set_timeout", "pause", "kill"):
        monkeypatch.setattr(AsyncSandbox, name, AsyncMock(side_effect=AssertionError(name)))
    resource = dict(
        provider="e2b",
        resource_id="sandbox",
        session_id="generation",
        project_id="project",
        workbench_id="workbench",
        workbench_generation="generation",
        template="template",
    )
    detail = SandboxDetail(
        template_id="template",
        sandbox_id="sandbox",
        client_id="client",
        started_at=datetime.now(UTC),
        end_at=datetime.now(UTC),
        envd_version="0.4.5",
        cpu_count=2,
        memory_mb=2048,
        disk_size_mb=1024,
        state=SandboxState.RUNNING,
        envd_access_token="fixture-transport-token",
        metadata={
            "agent_execution_id": "generation",
            "project_id": "project",
            "workbench_id": "workbench",
            "workbench_generation": "generation",
        },
        lifecycle=SandboxLifecycle(auto_resume=False, on_timeout=SandboxOnTimeout.PAUSE),
    )
    get = AsyncMock(return_value=SimpleNamespace(status_code=200, parsed=detail))
    monkeypatch.setattr(get_sandboxes_sandbox_id, "asyncio_detailed", get)
    return SimpleNamespace(resource=resource), detail, get


async def test_file_attachment_constructs_sdk_transport_without_resume_or_timeout(attachment):
    host, detail, get = attachment
    connection = await E2BRuntimeProvider().running_connection(host)
    connection.validate(host.resource)
    assert isinstance(connection.handle, AsyncSandbox)
    assert connection.handle.sandbox_id == detail.sandbox_id
    assert connection.handle.connection_config.http_version == "1.1"
    assert connection.handle._envd_access_token == detail.envd_access_token
    get.assert_awaited_once()


async def test_paused_file_attachment_never_connects_or_wakes(attachment):
    host, detail, _ = attachment
    detail.state = SandboxState.PAUSED
    with pytest.raises(WorkerDisconnected, match="lifecycle"):
        await E2BRuntimeProvider().running_connection(host)
    assert not hasattr(host, "sandbox")


@pytest.mark.parametrize(
    "field", ["agent_execution_id", "project_id", "workbench_id", "workbench_generation"]
)
async def test_foreign_file_attachment_rejected_before_transport(attachment, field):
    host, detail, _ = attachment
    detail.metadata[field] = "foreign"
    with pytest.raises(PermissionError, match="binding"):
        await E2BRuntimeProvider().running_connection(host)
    assert not hasattr(host, "sandbox")


@pytest.mark.parametrize("auto_resume", [True, None])
async def test_missing_or_unsafe_resume_policy_fails_closed(attachment, auto_resume):
    host, detail, _ = attachment
    detail.lifecycle = SimpleNamespace(auto_resume=auto_resume)
    with pytest.raises(PermissionError, match="automatic resume"):
        await E2BRuntimeProvider().running_connection(host)


async def test_service_disconnect_only_kills_the_bridge():
    wait = asyncio.Event()
    handle = SimpleNamespace(wait=wait.wait, disconnect=AsyncMock(), kill=AsyncMock())
    run = AsyncMock(return_value=handle)
    host = SimpleNamespace(
        sandbox=SimpleNamespace(
            commands=SimpleNamespace(run=run), kill=AsyncMock(), pause=AsyncMock()
        ),
        control_command=lambda: ["node", "/opt/puppyone-agent/workbench-file-client.mjs"],
        _output=AsyncMock(),
        _disconnected=AsyncMock(),
        connection=object(),
    )
    provider = E2BRuntimeProvider()
    await provider.open_service(host, "files")
    try:
        assert run.call_args.args == ("node /opt/puppyone-agent/workbench-file-client.mjs",)
        await provider.disconnect(host)
        await provider.close_channel(host)
        handle.kill.assert_awaited_once()
        host.sandbox.kill.assert_not_awaited()
        host.sandbox.pause.assert_not_awaited()
        assert host.connection is None
        with pytest.raises(ValueError, match="Unknown"):
            await provider.open_service(host, "other")
    finally:
        wait.set()
        await host.reader
