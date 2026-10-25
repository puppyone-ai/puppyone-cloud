"""Hosted SSH completion: resume through the sandbox module, pool-scoped reclamation,
bounded admission and a configurable host identity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncssh
import pytest
from postgrest.exceptions import APIError

from src.platform.access.adapters.ssh import gateway
from src.platform.access.adapters.ssh.hosted import HostedSshAccess, public_key
from src.platform.access.sessions import AccessSessions
from src.platform.authorization.models import AccessCapability
from src.platform.authorization.service import validate_access_shape
from src.platform.workspace.connections import ConnectionRepository, WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError

SHELL = {
    AccessCapability.WORKSPACE_READ,
    AccessCapability.WORKSPACE_WRITE,
    AccessCapability.WORKSPACE_SHELL,
}


class Opened:
    async def __aenter__(self):
        return ({"id": "connection"}, "shell")

    async def __aexit__(self, *exc):
        return False


def service(*, state="paused", generation="g", delegated=None, resumed=None):
    key = asyncssh.generate_private_key("ssh-ed25519")
    grant = dict(
        id=str(uuid4()),
        user_id="owner",
        project_id="p",
        workbench_id="w",
        generation="g",
        public_key=public_key(key.export_public_key()),
        expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        revoked_at=None,
        used_at=None,
        access_grant_id="access-grant" if delegated is not None else None,
    )
    row = {
        "id": "w",
        "user_id": "owner",
        "project_id": "p",
        "generation": generation,
        "state": state,
        "owner_deleted_at": None,
    }
    workspace = SimpleNamespace(
        repository=SimpleNamespace(get=Mock(return_value=row)),
        ensure_running=AsyncMock(return_value=resumed or {**row, "state": "running"}),
    )
    connections = SimpleNamespace(authorize=AsyncMock(), open=Mock(return_value=Opened()))
    authority = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(capabilities=delegated or set()))
    )
    access = HostedSshAccess(
        workspace,
        grants=Mock(get=Mock(return_value=grant), consume=Mock()),
        connections=connections,
        authority=authority,
        sessions=SimpleNamespace(finish_unallocated=AsyncMock()),
    )
    return access, key, grant, workspace, connections


async def test_paused_workspace_still_authenticates_without_waiting_for_compute():
    access, key, grant, workspace, connections = service()
    assert await access.authenticate(grant["id"], key)
    # Authority and identity are checked; only live compute is not required yet.
    assert connections.authorize.call_args.kwargs == {"running": False}
    workspace.ensure_running.assert_not_called()


async def test_owner_session_resumes_through_the_sandbox_module_before_connecting():
    access, _, grant, workspace, connections = service()
    notices = []

    async def notify(message):
        notices.append(message)

    async with access.open(grant["id"], notify=notify) as opened:
        assert opened == ({"id": "connection"}, "shell")
    workspace.ensure_running.assert_awaited_once()
    assert workspace.ensure_running.call_args.args[0] == {"user_id": "owner", "project_id": "p"}
    assert notices and "Resuming" in notices[0]
    connections.open.assert_called_once()


async def test_running_workspace_connects_without_an_open_request():
    access, _, grant, workspace, _ = service(state="running")
    async with access.open(grant["id"]):
        pass
    workspace.ensure_running.assert_not_called()


@pytest.mark.parametrize(
    "capabilities,resumes", [(SHELL, False), (SHELL | {AccessCapability.WORKSPACE_WAKE}, True)]
)
async def test_shared_session_resumes_only_with_the_wake_capability(capabilities, resumes):
    access, _, grant, workspace, connections = service(delegated=capabilities)
    if resumes:
        async with access.open(grant["id"]):
            pass
        workspace.ensure_running.assert_awaited_once()
    else:
        with pytest.raises(WorkspaceError, match="workbench_not_running"):
            async with access.open(grant["id"]):
                pass
        workspace.ensure_running.assert_not_called()
        connections.open.assert_not_called()


@pytest.mark.parametrize(
    "state,generation,resumed",
    [
        ("paused", "replaced", None),
        ("retired", "g", None),
        ("paused", "g", {"state": "running", "generation": "new"}),
    ],
)
async def test_replaced_or_retired_workspace_never_admits_an_old_credential(
    state, generation, resumed
):
    access, _, grant, _, connections = service(state=state, generation=generation, resumed=resumed)
    with pytest.raises(WorkspaceError):
        async with access.open(grant["id"]):
            pass
    connections.open.assert_not_called()


def test_connection_limit_is_an_actionable_refusal():
    client = Mock()
    client.rpc.return_value.execute.side_effect = APIError(
        {"message": "workspace_connection_limit", "code": "53400"}
    )
    with pytest.raises(WorkspaceError) as refused:
        ConnectionRepository(client).reserve(
            "id", {"id": "w", "generation": "g", "user_id": "u", "project_id": "p"}, {}
        )
    assert refused.value.status_code == 429 and refused.value.code == "workspace_connection_limit"


def test_reclamation_query_is_scoped_to_the_workspace_pool():
    client = Mock()
    query = client.table.return_value.select.return_value.eq.return_value
    query.neq.return_value.or_.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "c", "cloud_workbenches": {"worker_pool": "default"}}
    ]
    rows = ConnectionRepository(client).pending("default")
    assert (
        client.table.return_value.select.call_args.args[0]
        == "*, cloud_workbenches!inner(worker_pool)"
    )
    client.table.return_value.select.return_value.eq.assert_called_once_with(
        "cloud_workbenches.worker_pool", "default"
    )
    assert rows == [{"id": "c"}]


async def test_revoked_sessions_of_another_pool_are_left_to_that_pool():
    repository = Mock(
        call=Mock(
            return_value=[
                {"id": "mine", "workbench_id": "a"},
                {"id": "theirs", "workbench_id": "b"},
            ]
        )
    )
    pools = {"a": "default", "b": "dev-someone"}
    connections = WorkspaceConnections(
        Mock(), repository=Mock(get=lambda identity: {"worker_pool": pools[identity]})
    )
    connections.cleanup = AsyncMock()
    await AccessSessions(repository).reap(connections, "default")
    assert [call.args[0]["id"] for call in connections.cleanup.await_args_list] == ["mine"]


def test_wake_is_a_workspace_only_capability():
    validate_access_shape(
        "workspace",
        ["workspace.read", "workspace.write", "workspace.shell", "workspace.wake"],
        ["ssh"],
    )
    with pytest.raises(ValueError):
        validate_access_shape("project_root", ["repository.read", "workspace.wake"], ["git"])


def test_host_key_can_come_from_a_secret_variable(tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    config = SimpleNamespace(
        host_key=SimpleNamespace(get_secret_value=lambda: key.export_private_key().decode()),
        host_key_file="",
    )
    loaded = gateway.load_host_key(config)
    assert loaded.export_public_key() == key.export_public_key()
    missing = SimpleNamespace(
        host_key=SimpleNamespace(get_secret_value=lambda: ""), host_key_file=str(tmp_path / "none")
    )
    with pytest.raises(RuntimeError, match="WORKSPACE_SSH_HOST_KEY"):
        gateway.load_host_key(missing)


def test_gateway_process_refuses_connections_beyond_its_bound():
    connections = {object(), object()}
    server = gateway.SshServer(Mock(), connections, max_connections=2)
    connection = Mock()
    server.connection_made(connection)
    connection.close.assert_called_once()
    assert connection not in connections


async def test_refused_session_explains_itself_on_the_terminal():
    written = []
    process = SimpleNamespace(
        subsystem=None,
        get_extra_info=lambda name: "identity",
        stderr=SimpleNamespace(write=written.append, drain=AsyncMock()),
        exit=Mock(),
    )

    class Refusing:
        def open(self, identity, notify=None):
            raise WorkspaceError(409, {"code": "workbench_not_running"})

    await gateway.serve_shell(process, Refusing())
    process.exit.assert_called_once_with(1)
    assert b"cannot resume" in b"".join(written)


def test_sandbox_module_owns_connection_reclamation_and_project_cleanup(monkeypatch):
    from src.config import settings
    from src.platform.access.adapters.agent.runtime import worker

    monkeypatch.setattr(settings, "PROJECT_DELETION_CLEANUP_ENABLED", True)
    names = [name for name, _ in worker.sandbox_passes(Mock(), Mock(), "default")]
    assert names == [
        "session_workspace_retirement",
        "external_connection_reclamation",
        "project_deletion_cleanup",
    ]
    monkeypatch.setattr(settings, "PROJECT_DELETION_CLEANUP_ENABLED", False)
    assert "project_deletion_cleanup" not in [
        name for name, _ in worker.sandbox_passes(Mock(), Mock(), "default")
    ]


def test_gateway_no_longer_runs_its_own_reclamation_loop():
    import inspect

    assert "reap" not in inspect.getsource(gateway.run)


async def test_a_pasted_public_key_with_a_comment_authenticates():
    access, key, grant, _, _ = service(state="running")
    pasted = key.export_public_key().decode().strip() + " someone@laptop"
    grant["public_key"] = public_key(pasted)
    assert grant["public_key"] == public_key(key.export_public_key())
    assert await access.authenticate(grant["id"], key)
