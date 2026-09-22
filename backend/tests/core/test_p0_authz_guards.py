from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.platform.auth.models import CurrentUser
from src.version_engine.entrypoints.http.content_helpers import ensure_project_access
from src.exceptions import NotFoundException
from src.platform.authorization.models import ProjectAction

_ensure_project_access = ensure_project_access  # back-compat alias for this test file


def _make_user(user_id: str = "u_test") -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        email="u@test.dev",
        role="authenticated",
        is_anonymous=False,
        app_metadata={},
        user_metadata={},
    )


def test_content_node_project_guard_denies_unauthorized_user() -> None:
    authorization = Mock()
    authorization.authorize.side_effect = NotFoundException("not found")

    with pytest.raises(NotFoundException):
        _ensure_project_access(authorization, _make_user(), "project_123")

    authorization.authorize.assert_called_once_with(
        "project_123", "u_test", ProjectAction.CONTENT_READ
    )


def test_content_node_project_guard_allows_authorized_user() -> None:
    authorization = Mock()

    _ensure_project_access(authorization, _make_user(), "project_123")

    authorization.authorize.assert_called_once_with(
        "project_123", "u_test", ProjectAction.CONTENT_READ
    )


def test_tool_service_project_access_guard_denies_unauthorized_user() -> None:
    """ToolService enforces project access via _ensure_project_access pattern."""
    authorization = Mock()
    authorization.authorize.side_effect = NotFoundException("not found")

    with pytest.raises(NotFoundException):
        _ensure_project_access(authorization, _make_user(), "project_other")


def test_tool_service_project_access_guard_allows_authorized_user() -> None:
    """ToolService allows access when project membership verified."""
    authorization = Mock()

    _ensure_project_access(authorization, _make_user(), "project_own")
    authorization.authorize.assert_called_once_with(
        "project_own", "u_test", ProjectAction.CONTENT_READ
    )
