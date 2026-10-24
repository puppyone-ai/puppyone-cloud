"""Behavior at access boundaries: wire compatibility and kernel ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.access.adapters.api.workbench_router import (
    get_workbench_service,
    router,
    workspace_error_handler,
)
from src.platform.auth.dependencies import get_current_user
from src.platform.workspace.errors import WorkspaceError


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
