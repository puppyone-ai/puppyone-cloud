"""SSH delegation never widens the Workspace identity or skips cleanup admission."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncssh
import pytest

from src.platform.access.adapters.ssh.hosted import HostedSshAccess, public_key
from src.platform.workspace.connections import WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.execution.shell import WorkspaceShell


def setup():
    identity = str(uuid4())
    key = asyncssh.generate_private_key("ssh-ed25519")
    grant = dict(
        id=identity,
        user_id="u",
        project_id="p",
        workbench_id="w",
        generation="g",
        public_key=public_key(key.export_public_key()),
        expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        revoked_at=None,
        used_at=None,
    )
    grants = Mock(get=Mock(return_value=grant))
    connections = SimpleNamespace(authorize=AsyncMock(), renew=AsyncMock())
    service = HostedSshAccess(Mock(), grants=grants, connections=connections)
    return service, key, grant


async def test_key_proof_and_single_use_grant():
    service, key, grant = setup()
    assert await service.authenticate(grant["id"], key)
    assert not await service.authenticate(grant["id"], asyncssh.generate_private_key("ssh-ed25519"))
    grant["used_at"] = datetime.now(UTC).isoformat()
    assert not await service.authenticate(grant["id"], key)
    assert await service.validate(grant["id"]) == grant  # existing session only


@pytest.mark.parametrize("reason", ["expired", "revoked", "invalid", "forbidden"])
async def test_authentication_fails_before_execution(reason):
    service, key, grant = setup()
    if reason == "expired":
        grant["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    if reason == "revoked":
        grant["revoked_at"] = datetime.now(UTC).isoformat()
    if reason == "forbidden":
        service.connections.authorize.side_effect = PermissionError()
    assert not await service.authenticate("invalid" if reason == "invalid" else grant["id"], key)
    service.grants.consume.assert_not_called()


async def test_failed_stop_never_releases_occupancy():
    repository = Mock()
    connection = {"id": "session", "generation": "generation", "state": "active"}
    repository.update.return_value = {**connection, "state": "stopping"}
    shell = SimpleNamespace(stop=AsyncMock(side_effect=TimeoutError()), close=AsyncMock())
    service = WorkspaceConnections(Mock(), repository)
    with pytest.raises(TimeoutError):
        await service.cleanup(connection, shell)
    assert repository.update.call_args_list == [((connection, "stop"),)]


async def test_revocation_or_billing_denial_does_not_renew_remote_lease():
    workspace = SimpleNamespace(context=Mock(side_effect=PermissionError()))
    repository = Mock()
    shell = SimpleNamespace(control=AsyncMock())
    service = WorkspaceConnections(workspace, repository)
    connection = dict(user_id="u", project_id="p", workbench_id="w", generation="g")
    with pytest.raises(PermissionError):
        await service.renew(connection, shell)
    repository.update.assert_not_called()
    shell.control.assert_not_awaited()


def test_no_endpoint_without_pinned_host_key():
    service, _, _ = setup()
    service.config = SimpleNamespace(
        public_host="ssh.example", public_host_key="", public_port=2222
    )
    with pytest.raises(WorkspaceError) as error:
        service.endpoint()
    assert error.value.code == "workspace_ssh_not_configured"


def shell_fixture(provider):
    identity, workbench, generation, project = (str(uuid4()) for _ in range(4))
    connection = dict(
        id=identity,
        workbench_id=workbench,
        generation=generation,
        task_uid=20001,
        project_id=project,
        binding={"provider": provider, "artifact": "pinned"},
        resource=dict(
            task_execution_id=identity,
            task_uid=20001,
            workbench_id=workbench,
            workbench_generation=generation,
            project_id=project,
            provider=provider,
        ),
    )
    adapter = SimpleNamespace(
        name=provider,
        initialize=Mock(),
        running_connection=AsyncMock(),
        connect_task=AsyncMock(),
        open_channel=AsyncMock(),
    )
    return WorkspaceShell(connection, adapter=adapter), adapter


@pytest.mark.parametrize("provider", ["fly", "e2b", "docker"])
async def test_shell_reuses_provider_connection_and_reaper_only_inspects_running_resource(provider):
    shell, provider = shell_fixture(provider)
    shell.control = AsyncMock(
        return_value=dict(
            workspace_shell=1,
            workbench=shell.workbench,
            generation=shell.generation,
            execution=shell.identity,
        )
    )
    borrowed = object()
    await shell.connect(borrowed)
    provider.running_connection.assert_not_awaited()
    provider.connect_task.assert_awaited_once_with(shell, shell.resource, borrowed)
    await shell.connect()
    provider.running_connection.assert_awaited_once_with(shell)
    assert provider.connect_task.call_args.args[2] is provider.running_connection.return_value


async def test_slow_shell_output_cannot_block_cleanup_ack():
    shell, _ = shell_fixture("fly")
    for _ in range(shell.queue.maxsize + 1):
        await shell._event({"type": "shell_data", "data": "YQ=="})
    assert await shell.receive() == {"type": "disconnected"}
    future = asyncio.get_running_loop().create_future()
    shell.controls["stop"] = future
    await shell._output(
        json.dumps(
            {
                "type": "control_result",
                "sequence": 1,
                "id": "stop",
                "result": {},
            }
        )
        + "\n"
    )
    assert future.result() == {}


async def test_reaper_does_not_stop_a_connection_renewed_after_scan():
    connection = {"id": "session", "state": "active"}
    repository = Mock()
    repository.update.return_value = connection
    factory = Mock()
    service = WorkspaceConnections(Mock(), repository, shell_factory=factory)
    await service.cleanup(connection, expired=True)
    factory.assert_not_called()
    repository.update.assert_called_once_with(connection, "expire")
