"""Unit tests for shared project default workspace admission across members."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.platform.agent.runtime.control.shared_tasks import SharedTasks
from src.platform.workspace.connections import WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.screen import ScreenTickets, WorkspaceScreen
from src.platform.workspace.service import WorkbenchAdmission, WorkbenchService


def _shared_row(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "user_id": "user-a",
        "project_id": "proj-1",
        "name": "Workspace",
        "target_ref": "refs/heads/workspace",
        "is_default": True,
        "agent_registration_id": None,
        "owner_deleted_at": None,
        "state": "running",
        "generation": str(uuid4()),
        "compute_epoch": 1,
        "binding": {"provider": "docker", "artifact": "pinned", "profile": "default"},
        "resource": {"provider": "docker", "resource_id": "vm-1", "allocation_pending": False},
        "sandbox_settings": {},
    }
    row.update(overrides)
    return row


def test_admission_allows_second_member_on_shared_default_and_blocks_personal():
    shared = _shared_row()
    personal = _shared_row(is_default=False)
    external = _shared_row(is_default=False, agent_registration_id=str(uuid4()))
    other_project = _shared_row(project_id="proj-2")
    rows = {r["id"]: r for r in (shared, personal, external, other_project)}

    repo = SimpleNamespace(
        default=lambda user, project, rev: shared,
        get=lambda identity: rows.get(identity),
    )
    admission = WorkbenchAdmission(repository=repo, authorization=SimpleNamespace())
    ctx_b = SimpleNamespace(user_id="user-b", project_id="proj-1", policy={"revision": {"v": 1}})

    # Another project member can resolve the shared default workspace both implicitly and by ID.
    assert admission.admit(ctx_b)["id"] == shared["id"]
    assert admission.admit(ctx_b, shared["id"])["id"] == shared["id"]

    # Non-default personal, external-agent, and cross-project workbenches remain blocked.
    for denied in (personal, external, other_project):
        with pytest.raises(WorkspaceError) as exc:
            admission.admit(ctx_b, denied["id"])
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_service_check_and_sandbox_settings_pass_member_user():
    shared = _shared_row()
    context_calls = []
    settings_calls = []

    repo = SimpleNamespace(
        context=lambda user, project: {
            "facts": {"user": user},
            "revision": {"v": 1},
            "workbench": shared,
        },
        get=lambda identity: shared,
        set_sandbox_settings=lambda row, value, revision, **kw: (
            settings_calls.append((row["id"], value, kw)),
            {**row, "sandbox_settings": value},
        )[1],
    )
    auth = SimpleNamespace(
        authorize_facts=lambda facts, project, user, action: (
            context_calls.append((user, project, action)),
            SimpleNamespace(org_id="org-1"),
        )[1]
    )
    service = WorkbenchService(
        transport=SimpleNamespace(),
        filesystem=SimpleNamespace(),
        billing=SimpleNamespace(check_session=AsyncMock()),
        repository=repo,
        authorization=auth,
    )

    await service.check(shared, user="user-b")
    assert context_calls[-1][0] == "user-b"

    await service.set_sandbox_settings("user-b", "proj-1", {"desktop": True})
    assert settings_calls[-1] == (shared["id"], {"desktop": True}, {"user": "user-b"})

    # Creator call omits user kwarg to preserve compatibility with existing mocks.
    await service.set_sandbox_settings("user-a", "proj-1", {"desktop": True})
    assert settings_calls[-1] == (shared["id"], {"desktop": True}, {})


@pytest.mark.asyncio
async def test_lifecycle_request_allows_member_on_shared_default_and_blocks_personal():
    shared = _shared_row(state="paused")
    personal = _shared_row(state="paused", is_default=False)
    open_calls = []

    repo = SimpleNamespace(
        context=lambda user, project: {
            "facts": {"user": user},
            "revision": {"v": 1},
            "workbench": shared,
        },
        default=lambda user, project, rev: shared,
        request_open=lambda row, binding, revision, pool, **kw: (
            open_calls.append((row["id"], kw)),
            {**row, "open_requested": True},
        )[1],
    )
    auth = SimpleNamespace(
        authorize_facts=lambda facts, project, user, action: SimpleNamespace(org_id="org-1")
    )
    provider = SimpleNamespace(
        name="docker",
        artifact=lambda row: "pinned",
        capabilities=SimpleNamespace(streams=()),
    )
    service = WorkbenchService(
        transport=SimpleNamespace(),
        filesystem=SimpleNamespace(),
        billing=SimpleNamespace(),
        repository=repo,
        authorization=auth,
        providers=lambda name=None: provider,
    )
    service.lifecycle.preflight = AsyncMock(return_value=provider)

    result = await service.request_open("user-b", "proj-1")
    assert result["open_requested"] is True
    assert open_calls[-1] == (shared["id"], {"user": "user-b"})

    with pytest.raises(PermissionError):
        await service.request_open("user-b", "proj-1", row=personal)


@pytest.mark.asyncio
async def test_connections_authorize_and_open_forward_member_user():
    shared = _shared_row()
    external = _shared_row(is_default=False, agent_registration_id=str(uuid4()))
    check_calls = []
    reserve_calls = []

    class FakeShell:
        def __init__(self, conn):
            self.conn = conn

        async def connect(self, handle):
            return None

        async def stop(self):
            return None

        async def close(self):
            return None

    workspace = SimpleNamespace(
        context=lambda user, project, action: (
            {"revision": {"v": 1}, "workbench": shared},
            SimpleNamespace(org_id="org-1"),
        ),
        repository=SimpleNamespace(get=lambda wid: external if wid == external["id"] else shared),
        check=AsyncMock(side_effect=lambda row, **kw: check_calls.append((row["id"], kw))),
        connection=AsyncMock(return_value="ssh-handle"),
    )
    conn_repo = SimpleNamespace(
        reserve=lambda identity, row, revision, session=None, **kw: (
            reserve_calls.append((identity, row["id"], kw)),
            {"id": identity, "workbench_id": row["id"], "generation": row["generation"]},
        )[1],
        update=MagicMock(return_value={"state": "stopping"}),
    )
    connections = WorkspaceConnections(
        workspace=workspace, repository=conn_repo, shell_factory=FakeShell
    )

    conn_id = str(uuid4())
    async with connections.open(conn_id, "user-b", "proj-1", shared["id"], shared["generation"]):
        pass
    assert check_calls == [(shared["id"], {"user": "user-b"})]
    assert reserve_calls == [(conn_id, shared["id"], {"user": "user-b"})]

    with pytest.raises(WorkspaceError) as exc:
        await connections.authorize("user-b", "proj-1", external["id"], external["generation"])
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_screen_ticket_binds_and_rechecks_viewing_member(monkeypatch):
    shared = _shared_row()
    context_users = []

    service = SimpleNamespace(
        context=lambda user, project, action: (
            context_users.append(user),
            ({"revision": {"v": 1}, "workbench": shared}, SimpleNamespace(org_id="org-1")),
        )[1],
        connection=AsyncMock(return_value="handle"),
    )
    tickets = ScreenTickets(secret="test-screen-secret")
    screen = WorkspaceScreen(service=service, tickets=tickets)
    monkeypatch.setattr(
        "src.platform.workspace.screen.screen_feature",
        lambda row: {"id": "screen_stream", "enabled": True, "preview": False, "transport": "vnc"},
    )
    monkeypatch.setattr(
        "src.platform.workspace.screen.runtime_provider",
        lambda name: SimpleNamespace(capabilities=SimpleNamespace(streams=("screen",))),
    )

    issued = await screen.session("user-b", "proj-1")
    row, ctx = await screen.admit("proj-1", issued["ticket"])
    assert row["id"] == shared["id"]
    assert ctx["user_id"] == "user-b"
    assert context_users == ["user-b", "user-b"]


@pytest.mark.asyncio
async def test_shared_tasks_admits_member_run_on_shared_default_and_blocks_personal():
    shared = _shared_row()
    personal = _shared_row(is_default=False)
    rows = {shared["id"]: shared, personal["id"]: personal}
    repo = SimpleNamespace(workbench=lambda wid: rows.get(wid))
    tasks = SharedTasks(repository=repo, owner=SimpleNamespace())

    admitted = await tasks.workbench(
        {"workbench_id": shared["id"], "user_id": "user-b", "project_id": "proj-1"}
    )
    assert admitted["id"] == shared["id"]

    with pytest.raises(PermissionError):
        await tasks.workbench(
            {"workbench_id": personal["id"], "user_id": "user-b", "project_id": "proj-1"}
        )
