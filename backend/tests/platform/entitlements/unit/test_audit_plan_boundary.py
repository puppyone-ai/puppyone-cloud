"""Audit queries require both project authority and the Space's paid feature."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.exception_handler import app_exception_handler
from src.exceptions import AppException, ErrorCode
from src.platform.access.adapters.api import audit
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.entitlements.dependencies import get_entitlement_service


@pytest.mark.parametrize("allowed", [False, True])
def test_audit_access_checks_the_authorized_project_space_before_reading(allowed):
    app = FastAPI()
    app.include_router(audit.router)
    app.add_exception_handler(AppException, app_exception_handler)
    authorization = Mock()
    authorization.authorize.return_value = SimpleNamespace(org_id="space-a")
    entitlements = Mock()
    if not allowed:
        entitlements.require_feature.side_effect = AppException(
            code=ErrorCode.FORBIDDEN, status_code=403, message="Audit requires Enterprise")
    repository = Mock()
    repository.list_by_project.return_value = []
    repository.count_by_project.return_value = 0
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u1", role="authenticated")
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_entitlement_service] = lambda: entitlements
    app.dependency_overrides[audit._get_audit_repo] = lambda: repository
    with TestClient(app) as client:
        response = client.get("/nodes/project-audit-logs", params={"project_id": "project-a"})
    assert response.status_code == (200 if allowed else 403)
    entitlements.require_feature.assert_called_once_with("space-a", "activity.log")
    if allowed:
        repository.list_by_project.assert_called_once_with("project-a", 100, 0)
    else:
        repository.list_by_project.assert_not_called()
