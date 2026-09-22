"""C-3 — /internal/* endpoints must declare X-Acting-User-Id and verify the
acting user has access to the targeted project.

The vulnerability: holders of the internal SECRET could read/write any
project by varying the project_id payload. After this fix, every
project-scoped /internal/nodes/* endpoint additionally requires
X-Acting-User-Id and verifies that user has access.

These tests exercise the helper directly so they don't depend on a live
FastAPI request lifecycle (which would still need DB stubs).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.infra.search.schemas import SearchToolQueryInput
from src.internal import router as internal_router
from src.internal.router import _enforce_acting_user_project_access
from tests.authorization_fakes import authorization_for


def _fake_request(headers: dict[str, str]) -> Request:
    """Build a minimal Request with the given headers."""
    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
    }
    return Request(scope)


def test_missing_project_id_returns_400():
    req = _fake_request({"x-acting-user-id": "user-1"})
    with pytest.raises(HTTPException) as exc:
        _enforce_acting_user_project_access(req, "")
    assert exc.value.status_code == 400


def test_missing_acting_user_returns_400():
    """Even with a valid secret, a project-scoped call without the
    acting-user header must be rejected."""
    req = _fake_request({})  # no header
    with pytest.raises(HTTPException) as exc:
        _enforce_acting_user_project_access(req, "project-x")
    assert exc.value.status_code == 400
    assert "X-Acting-User-Id" in str(exc.value.detail)


def test_acting_user_with_no_access_returns_403():
    """Acting user that doesn't belong to the project → 403."""
    req = _fake_request({"x-acting-user-id": "intruder-uuid"})
    with patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization_for(),
    ):
        with pytest.raises(HTTPException) as exc:
            _enforce_acting_user_project_access(req, "project-x")
    assert exc.value.status_code == 403


def test_acting_user_with_access_returns_user_id():
    """Acting user that DOES have access → returns the user_id (the auth
    helper's contract, used by handlers to know who is operating)."""
    req = _fake_request({"x-acting-user-id": "alice-uuid"})
    with patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization_for("project-x"),
    ):
        result = _enforce_acting_user_project_access(req, "project-x")
    assert result == "alice-uuid"


def test_db_error_during_check_returns_503():
    """If the access check itself errors (DB outage etc.), fail closed
    with a transient 503 — never a quiet allow."""
    req = _fake_request({"x-acting-user-id": "alice-uuid"})
    authorization = MagicMock()
    authorization.allows.side_effect = RuntimeError("DB down")
    with patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization,
    ):
        with pytest.raises(HTTPException) as exc:
            _enforce_acting_user_project_access(req, "project-x")
    assert exc.value.status_code == 503


def test_project_scoped_route_manifest_is_guarded():
    """Every route in the security manifest must call a central actor guard."""
    routes = {
        route.path: route.endpoint
        for route in internal_router.router.routes
        if route.path in internal_router.PROJECT_SCOPED_INTERNAL_ENDPOINTS
    }
    assert set(routes) == internal_router.PROJECT_SCOPED_INTERNAL_ENDPOINTS
    for path, endpoint in routes.items():
        source = inspect.getsource(endpoint)
        assert (
            "_enforce_acting_user_project_access" in source
            or "_enforce_acting_user_table_access" in source
        ), f"{path} lacks the centralized project actor guard"


def test_tool_search_rejects_missing_actor_before_search():
    tool_repo = MagicMock()
    tool_repo.get_tool.return_value = SimpleNamespace(
        type="search",
        path="docs",
        project_id="project-x",
        json_path="",
    )
    search_service = MagicMock()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            internal_router.search_tool(
                "tool-1",
                SearchToolQueryInput(query="needle", top_k=3),
                _fake_request({}),
                supabase_repo=tool_repo,
                search_service=search_service,
            )
        )
    assert exc.value.status_code == 400
    search_service.search_folder.assert_not_called()
    search_service.search_scope.assert_not_called()


def test_tool_search_rejects_non_member_before_search():
    tool_repo = MagicMock()
    tool_repo.get_tool.return_value = SimpleNamespace(
        type="search",
        path="docs",
        project_id="project-x",
        json_path="",
    )
    search_service = MagicMock()
    with patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization_for(),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                internal_router.search_tool(
                    "tool-1",
                    SearchToolQueryInput(query="needle", top_k=3),
                    _fake_request({"x-acting-user-id": "intruder"}),
                    supabase_repo=tool_repo,
                    search_service=search_service,
                )
            )
    assert exc.value.status_code == 403
    search_service.search_folder.assert_not_called()
    search_service.search_scope.assert_not_called()
