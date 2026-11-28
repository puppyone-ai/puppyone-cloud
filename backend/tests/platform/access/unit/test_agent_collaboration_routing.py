"""Unit tests for Access + SSH routing through the pluggable collaboration router."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from src.config import settings
from src.platform.access import agent_links
from src.platform.access.adapters.ssh.hosted import HostedSshAccess, SshSession
from src.platform.access.agent_links import AgentLinkService
from src.platform.access.credentials import CredentialService
from src.platform.workspace.collaboration import AgentCollaborationRouter
from src.platform.workspace.errors import WorkspaceError

CONNECTION = "11111111-1111-4111-8111-111111111111"
ENDPOINT = {"host": "gateway.fixture", "port": 2222, "known_hosts": "[gateway.fixture]:2222 key"}


def test_agent_instructions_adapt_to_collaboration_mode(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture")
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    shared_page = agent_links.agent_instructions("pai_" + "a" * 43)
    assert "shared workspace" in shared_page
    assert "appear\nimmediately" in shared_page
    assert "your person decides when they\njoin the project" not in shared_page

    isolated_page = agent_links.agent_instructions("pai_" + "a" * 43, mode="isolated_branch")
    assert "your own copy of the project" in isolated_page
    assert "your person decides when they\njoin the project" in isolated_page

    tier2_page = agent_links.agent_instructions("pai_" + "a" * 43, mode="fly_host_export")
    assert "your own isolated machine mounted onto this project's shared" in tier2_page

    tier3_page = agent_links.agent_instructions("pai_" + "a" * 43, mode="juicefs_shared")
    assert "your own isolated machine mounted onto this project's shared" in tier3_page


@pytest.mark.asyncio
async def test_agent_link_service_passes_mode_and_uses_actor_user_id(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    calls = []

    def call(name, **kwargs):
        calls.append((name, kwargs))
        if name == "access_agent_workspace":
            return {
                "id": "wb-shared",
                "user_id": "former-creator",
                "actor_user_id": "active-admin",
                "project_id": "proj-1",
                "state": "retired",
                "collaboration_mode": "shared_machine",
            }
        if name == "access_agent_ssh_ticket":
            return {"id": "ticket-1"}
        raise AssertionError(name)

    repo = SimpleNamespace(
        call=Mock(side_effect=call),
        command=Mock(
            return_value={
                "links": [],
                "connections": [
                    {
                        "id": CONNECTION,
                        "name": "Grok",
                        "branch": "refs/heads/workspace",
                        "collaboration_mode": "shared_machine",
                    }
                ],
                "pending": [],
            }
        ),
        agent_saves=Mock(
            return_value={
                "refs/heads/workspace": {
                    "status": "saved",
                    "error": None,
                    "finished_at": "2026-11-24T00:00:00Z",
                    "unsaved_work": False,
                }
            }
        ),
    )
    workspaces = SimpleNamespace(request_open=AsyncMock())
    service = AgentLinkService(repo, endpoint=lambda: ENDPOINT, workspaces=workspaces)

    await service.prepare(CONNECTION)
    assert calls[0] == ("access_agent_workspace", {"id": CONNECTION, "mode": "shared_machine"})
    # Uses active approving admin (actor_user_id), not the former creator on the shared row.
    workspaces.request_open.assert_awaited_once()
    assert workspaces.request_open.call_args.args[0] == "active-admin"

    assert await service.ticket(CONNECTION) == "ticket-1"
    assert calls[1] == ("access_agent_ssh_ticket", {"id": CONNECTION, "mode": "shared_machine"})

    inv = await service.inventory("active-admin", "proj-1")
    assert inv["collaboration_mode"] == "shared_machine"
    assert inv["connections"][0]["save"]["status"] == "saved"


@pytest.mark.asyncio
async def test_agent_link_service_fails_closed_for_unimplemented_project_override(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    repo = SimpleNamespace(
        call=Mock(
            return_value={
                "id": "wb-1",
                "user_id": "admin",
                "project_id": "proj-1",
                "state": "retired",
                "collaboration_mode": "juicefs_shared",
            }
        )
    )
    service = AgentLinkService(repo, endpoint=lambda: ENDPOINT)
    with pytest.raises(WorkspaceError) as exc:
        await service.workspace(CONNECTION)
    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "code": "agent_collaboration_mode_not_ready",
        "mode": "juicefs_shared",
    }


@pytest.mark.asyncio
async def test_agent_link_service_admits_tier2_fly_host_export_and_wakes_host_first(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    host_row = {
        "id": "wb-host",
        "user_id": "owner",
        "project_id": "proj-1",
        "state": "paused",
        "open_requested": False,
        "is_default": True,
    }
    agent_row = {
        "id": "wb-agent",
        "user_id": "owner",
        "actor_user_id": "owner",
        "project_id": "proj-1",
        "state": "retired",
        "collaboration_mode": "fly_host_export",
        "host_workbench_id": "wb-host",
    }
    repo = SimpleNamespace(
        call=Mock(return_value=agent_row),
        command=Mock(return_value={"links": [], "connections": [], "pending": []}),
        agent_saves=Mock(return_value={}),
        project_collab_mode=Mock(return_value="fly_host_export"),
    )
    workspaces = SimpleNamespace(
        repository=SimpleNamespace(get=Mock(return_value=host_row)),
        request_open=AsyncMock(),
    )
    service = AgentLinkService(repo, endpoint=lambda: ENDPOINT, workspaces=workspaces)

    await service.prepare(CONNECTION)
    assert [call.args[2]["id"] for call in workspaces.request_open.await_args_list] == [
        "wb-host",
        "wb-agent",
    ]
    inv = await service.inventory("owner", "proj-1")
    assert inv["collaboration_mode"] == "fly_host_export"


@pytest.mark.asyncio
async def test_hosted_ssh_admit_resume_and_open_attribution_in_shared_machine(monkeypatch, caplog):
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    ticket_id = str(uuid4())
    grant = {
        "id": ticket_id,
        "user_id": "approving-admin",
        "project_id": "proj-1",
        "workbench_id": "wb-shared",
        "generation": "gen-1",
        "public_key": "ssh-ed25519 AAAA",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "revoked_at": None,
        "used_at": None,
        "access_grant_id": "grant-1",
    }
    shared_row = {
        "id": "wb-shared",
        "user_id": "former-creator",
        "project_id": "proj-1",
        "generation": "gen-1",
        "state": "paused",
        "owner_deleted_at": None,
        "is_default": True,
        "agent_registration_id": None,
    }

    ensure_calls = []

    async def ensure_running(run, row):
        ensure_calls.append(run)
        return {**row, "state": "running"}

    workspace = SimpleNamespace(
        repository=SimpleNamespace(get=Mock(return_value=shared_row)),
        ensure_running=AsyncMock(side_effect=ensure_running),
    )
    grants = SimpleNamespace(get=Mock(return_value=grant), consume=Mock())
    authority = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(capabilities=("workspace.wake",)))
    )

    @asynccontextmanager
    async def open_conn(*args, **kwargs):
        yield ({"id": args[0]}, SimpleNamespace())

    connections = SimpleNamespace(authorize=AsyncMock(), open=open_conn)
    before_mock = AsyncMock()
    router = AgentCollaborationRouter()
    monkeypatch.setattr(router.resolve("shared_machine"), "before_agent_session", before_mock)
    linked = SimpleNamespace(
        collaboration=router,
        workspace=AsyncMock(return_value={**shared_row, "state": "running"}),
        ticket=AsyncMock(return_value=ticket_id),
    )

    service = HostedSshAccess(
        workspace,
        grants=grants,
        connections=connections,
        authority=authority,
        sessions=SimpleNamespace(),
        agent_links=linked,
    )

    session = await service.admit(f"agent-{CONNECTION}")
    assert isinstance(session, SshSession)
    before_mock.assert_awaited_once_with(workspace, grant)

    with caplog.at_level(logging.INFO, logger="src.platform.access.adapters.ssh.hosted"):
        async with service.open(session):
            pass

    # Resume uses grant["user_id"] (approving admin), not former-creator from shared_row.
    assert ensure_calls == [{"user_id": "approving-admin", "project_id": "proj-1"}]
    messages = [record.message for record in caplog.records]
    assert "external_agent_shell_opened" in messages
    assert "external_agent_shell_closed" in messages


@pytest.mark.asyncio
async def test_credential_service_routes_ssh_through_collaboration_router(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    repo = SimpleNamespace(call=Mock(return_value={"id": str(uuid4())}))
    service = CredentialService(repository=repo)
    ssh_req = SimpleNamespace(
        request_id=uuid4(),
        grant_id=uuid4(),
        channel="ssh",
        next_secret=SecretStr("par_" + "a" * 48),
        credential_secret=None,
        surface_id=None,
        expires_in=900,
    )
    await service._issue(["hash"], ssh_req, "ssh-ed25519 AAAA")
    assert repo.call.call_args.args[0] == "access_issue_credential"
    assert repo.call.call_args.kwargs["mode"] == "shared_machine"

    # Non-SSH channels do not pass workspace collaboration mode.
    repo.call.reset_mock()
    mcp_req = SimpleNamespace(
        request_id=uuid4(),
        grant_id=uuid4(),
        channel="mcp",
        next_secret=SecretStr("par_" + "b" * 48),
        credential_secret=SecretStr("mcp_" + "c" * 48),
        surface_id="surf-1",
        expires_in=900,
    )
    await service._issue(["hash"], mcp_req, None)
    assert "mode" not in repo.call.call_args.kwargs
