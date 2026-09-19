"""Navigation's lightweight projection keeps the exact Human authorization gate."""
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.project.router as routes
from src.exception_handler import app_exception_handler
from src.exceptions import AppException, ServiceUnavailableException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.repository import ProjectAuthorizationFacts
from src.platform.authorization.service import AuthorizationService
from src.platform.project.dependencies import get_project_service
from src.platform.project.models import Project


@pytest.fixture
def navigation(monkeypatch):
    projects = [Project(id=pid, org_id="org", name=pid, created_at=datetime.now(UTC))
                for pid in ["visible", "hidden"]]
    facts = Mock()
    facts.load_project_facts_batch.return_value = {
        pid: ProjectAuthorizationFacts(project_id=pid, org_id="org", visibility="private",
                                       org_role="member", project_role="viewer" if pid == "visible" else None,
                                       project_member_org_id="org" if pid == "visible" else None)
        for pid in ["visible", "hidden"]
    }
    service = Mock()
    service.get_by_org_id.return_value = projects
    counts = Mock(return_value={"visible": 7})
    monkeypatch.setattr(routes, "resolve_org_ids", lambda org_id, user_id: ["org"])
    monkeypatch.setattr(routes, "_count_user_access_points", counts)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="user", email="user@example.com", role="authenticated")
    app.dependency_overrides[get_project_service] = lambda: service
    app.dependency_overrides[get_authorization_service] = lambda: AuthorizationService(facts)
    with TestClient(app) as client:
        yield client, counts, facts, service


def test_navigation_skips_counts_but_keeps_grants_and_filters_private_projects(navigation):
    client, counts, facts, service = navigation
    response = client.get("/api/v1/projects/?org_id=org&include_access_counts=false")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["id"] for row in rows] == ["visible"]
    assert rows[0]["effective_role"] == "viewer"
    assert rows[0]["access_point_count"] is None  # unrequested, not a false zero
    counts.assert_not_called()
    facts.load_project_facts_batch.assert_called_once()
    service.get_by_org_id.assert_called_once_with("org")


def test_legacy_list_still_counts_only_authorized_projects(navigation):
    client, counts, _, _ = navigation
    rows = client.get("/api/v1/projects/?org_id=org").json()["data"]
    assert rows[0]["access_point_count"] == 7
    counts.assert_called_once_with(["visible"])


def test_authorization_failure_is_not_a_successful_empty_list(navigation):
    client, counts, facts, _ = navigation
    facts.load_project_facts_batch.side_effect = RuntimeError("database offline")
    response = client.get("/api/v1/projects/?include_access_counts=false")
    assert response.status_code == 503
    counts.assert_not_called()


def test_project_read_failure_is_not_a_successful_empty_list(navigation):
    client, counts, _, service = navigation
    service.get_by_org_id.side_effect = ServiceUnavailableException("temporarily unavailable")
    response = client.get("/api/v1/projects/?include_access_counts=false")
    assert response.status_code == 503
    counts.assert_not_called()
