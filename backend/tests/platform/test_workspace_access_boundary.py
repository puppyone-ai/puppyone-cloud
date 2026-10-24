"""Behavior at access boundaries: wire compatibility and kernel ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.access.adapters.api.workbench_router import (
    get_workbench_service,
    router,
    workspace_error_handler,
)
from src.platform.access.adapters.ssh.service import SshAccessService
from src.platform.auth.dependencies import get_current_user
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.scopes.service import WorkspaceConnection


def test_live_file_conflict_keeps_existing_http_contract():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(WorkspaceError, workspace_error_handler)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="user")
    operation = AsyncMock(side_effect=WorkspaceError(409, {"code": "file_conflict"}))
    app.dependency_overrides[get_workbench_service] = lambda: SimpleNamespace(
        file_operation=operation
    )
    generation = str(uuid4())
    response = TestClient(app).get(
        f"/api/v1/projects/project/workbench/file?generation={generation}&path=README.md"
    )
    assert response.status_code == 409
    assert response.json()["data"] == {"code": "file_conflict"}
    operation.assert_awaited_once_with(
        "user", "project", generation, {"action": "read", "path": "README.md"}
    )


async def test_ssh_adapter_only_uses_workspace_connection_api():
    connection = WorkspaceConnection(
        provider="fly",
        state="running",
        via="reused",
        host="workspace.example",
        port=22,
        username="user",
        proxy_command=None,
        workspace_path="/home/user/repo",
        expires_at=1000,
        connected_users=1,
    )
    # Deliberately no provider, manager, repository or lifecycle internals.
    workspace = SimpleNamespace(
        connect=AsyncMock(return_value=connection),
        revoke=AsyncMock(return_value=0),
        status=Mock(return_value={"state": "none", "connected": False, "connected_users": 0}),
    )
    adapter = SshAccessService(workspace)
    request = dict(project_id="project", scope_id="scope", user_id="user", public_key="key")
    result = await adapter.connect(**request)
    workspace.connect.assert_awaited_once_with(**request)
    assert "HostName workspace.example" in result.ssh_config_block
    assert result.workspace_path == connection.workspace_path
    assert not hasattr(connection, "ssh_config_block")
    assert adapter.status(project_id="project", scope_id="scope", user_id="user")["state"] == "none"
    assert await adapter.revoke(project_id="project", scope_id="scope", user_id="user") == 0
    workspace.revoke.assert_awaited_once_with(
        project_id="project", scope_id="scope", user_id="user"
    )
