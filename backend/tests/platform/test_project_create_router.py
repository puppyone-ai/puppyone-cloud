from __future__ import annotations

from datetime import UTC, datetime
from threading import get_ident

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.project.router as project_router_module
from src.exception_handler import app_exception_handler
from src.exceptions import AppException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import (
    ROLE_CAPABILITIES,
    GrantSource,
    ProjectGrant,
    ProjectRole,
)
from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.project.control_plane import (
    IdempotentProjectResult,
    ProjectDeletionResult,
)
from src.platform.project.control_plane_dependencies import get_project_control_plane_service
from src.platform.project.dependencies import get_project_repository
from src.platform.project.models import Project
from src.platform.project.write_lease import get_project_write_lease_factory
from src.version_engine.bootstrap.dependencies import get_version_write_engine

OPERATION_KEY = "123e4567-e89b-42d3-a456-426614174000"


def _project() -> Project:
    timestamp = datetime(2026, 7, 16, tzinfo=UTC)
    return Project(
        id="project-1",
        name="Local repository",
        description="",
        org_id="org-1",
        visibility="org",
        bound_git_branch="main",
        created_by="user-1",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _grant() -> ProjectGrant:
    return ProjectGrant(
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        role=ProjectRole.ADMIN,
        source=GrantSource.PROJECT_MEMBER,
        capabilities=ROLE_CAPABILITIES[ProjectRole.ADMIN],
    )


class ControlPlaneStub:
    def __init__(self, replay_sequence=(False,)):
        self.replays = list(replay_sequence)
        self.ready = True
        self.create_calls: list[dict] = []
        self.create_threads: list[int] = []
        self.complete_calls: list[dict] = []
        self.complete_threads: list[int] = []
        self.abandon_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.abandon_result = ProjectDeletionResult(
            project_id="project-1",
            deletion_job_id="job-1",
            status="pending",
            replayed=False,
        )
        self.delete_result = ProjectDeletionResult(
            project_id="project-1",
            deletion_job_id="job-1",
            status="pending",
        )

    def create_project(self, **kwargs):
        self.create_threads.append(get_ident())
        self.create_calls.append(kwargs)
        return IdempotentProjectResult(
            project=_project(),
            replayed=self.replays.pop(0),
            ready=self.ready,
        )

    def abandon_initialization(self, **kwargs):
        self.abandon_calls.append(kwargs)
        return self.abandon_result

    def delete_project(self, **kwargs):
        self.delete_calls.append(kwargs)
        return self.delete_result

    def complete_project_initialization(self, **kwargs):
        self.complete_threads.append(get_ident())
        self.complete_calls.append(kwargs)
        return IdempotentProjectResult(
            project=_project(),
            replayed=kwargs["replayed"],
            ready=True,
        )


class VersionEngineStub:
    def __init__(self):
        self.initialized: list[str] = []
        self.event_loop_threads: list[int] = []

    async def initialize_project_tree(self, project_id: str):
        self.event_loop_threads.append(get_ident())
        self.initialized.append(project_id)


class EntitlementStub:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.threads: list[int] = []

    def enforced_limit_value(self, org_id, key):
        self.threads.append(get_ident())
        self.calls.append((org_id, key))
        return 5


class AuthorizationStub:
    def __init__(self):
        self.threads: list[int] = []

    def authorize(self, project_id, user_id, _action):
        self.threads.append(get_ident())
        assert (project_id, user_id) == ("project-1", "user-1")
        return _grant()


class ProjectRepositoryStub:
    def get_by_id(self, project_id):
        assert project_id == "project-1"
        return _project()


class NoopWriteLease:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _app(monkeypatch, control_plane: ControlPlaneStub | None = None):
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(project_router_module.router, prefix="/api/v1")
    control_plane = control_plane or ControlPlaneStub()
    entitlements = EntitlementStub()
    version_engine = VersionEngineStub()
    authorization = AuthorizationStub()
    access_count_threads: list[int] = []
    request_loop_threads: list[int] = []

    async def current_user_override():
        request_loop_threads.append(get_ident())
        return CurrentUser(user_id="user-1", email="user@example.com", role="authenticated")

    app.dependency_overrides[get_current_user] = current_user_override
    app.dependency_overrides[get_project_control_plane_service] = lambda: control_plane
    app.dependency_overrides[get_entitlement_service] = lambda: entitlements
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_project_repository] = ProjectRepositoryStub
    app.dependency_overrides[get_version_write_engine] = lambda: version_engine
    app.dependency_overrides[get_project_write_lease_factory] = lambda: NoopWriteLease

    def count_access_points(_ids):
        access_count_threads.append(get_ident())
        return {}

    monkeypatch.setattr(project_router_module, "_count_user_access_points", count_access_points)
    app.state.test_authorization = authorization
    app.state.test_access_count_threads = access_count_threads
    app.state.test_request_loop_threads = request_loop_threads
    return app, control_plane, entitlements, version_engine


def _body(**updates):
    body = {
        "name": "Local repository",
        "description": "",
        "org_id": "org-1",
    }
    body.update(updates)
    return body


def test_create_requires_explicit_org_and_idempotency_key(monkeypatch):
    app, control_plane, _, _ = _app(monkeypatch)
    client = TestClient(app)

    missing_key = client.post("/api/v1/projects/", json=_body())
    missing_org = client.post(
        "/api/v1/projects/",
        headers={"Idempotency-Key": OPERATION_KEY},
        json=_body(org_id=None),
    )

    assert missing_key.status_code == 400
    assert missing_key.json()["data"]["code"] == "idempotency_key_required"
    assert missing_org.status_code == 422
    assert missing_org.json()["detail"][0]["loc"][-1] == "org_id"
    assert control_plane.create_calls == []


def test_create_and_exact_replay_have_stable_body_and_explicit_replay_header(monkeypatch):
    control_plane = ControlPlaneStub((False, True))
    app, _, entitlements, _ = _app(monkeypatch, control_plane)
    client = TestClient(app)
    headers = {"Idempotency-Key": OPERATION_KEY}

    created = client.post("/api/v1/projects/", headers=headers, json=_body())
    replayed = client.post("/api/v1/projects/", headers=headers, json=_body())

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert created.headers["Idempotency-Replayed"] == "false"
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert created.json() == replayed.json()
    assert [call["org_id"] for call in control_plane.create_calls] == ["org-1", "org-1"]
    assert [call["operation_key"] for call in control_plane.create_calls] == [
        OPERATION_KEY,
        OPERATION_KEY,
    ]
    assert all(call["publication_mode"] == "empty" for call in control_plane.create_calls)
    assert all(
        call["source_fingerprint"] == {
            "kind": "empty-git-repository",
            "version": 1,
        }
        for call in control_plane.create_calls
    )
    assert entitlements.calls == [
        ("org-1", "projects.max"),
        ("org-1", "projects.max"),
    ]


def test_create_returns_only_after_l5_initializes_and_control_plane_marks_ready(monkeypatch):
    control_plane = ControlPlaneStub((False,))
    control_plane.ready = False
    app, _, entitlements, version_engine = _app(monkeypatch, control_plane)

    response = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Idempotency-Key": OPERATION_KEY},
        json=_body(),
    )

    assert response.status_code == 201
    assert version_engine.initialized == ["project-1"]
    assert control_plane.complete_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
            "replayed": False,
        }
    ]
    event_loop_thread = app.state.test_request_loop_threads[0]
    assert version_engine.event_loop_threads[0] != event_loop_thread
    assert entitlements.threads[0] != event_loop_thread
    assert control_plane.create_threads[0] != event_loop_thread
    assert control_plane.complete_threads[0] != event_loop_thread
    assert app.state.test_authorization.threads[0] != event_loop_thread
    assert app.state.test_access_count_threads[0] != event_loop_thread


def test_empty_create_schema_forbids_seed_and_template_compatibility_fields(monkeypatch):
    app, control_plane, _, _ = _app(monkeypatch)
    client = TestClient(app)

    for body in (_body(seed=True), _body(template="get-started")):
        response = client.post(
            "/api/v1/projects/",
            headers={"Idempotency-Key": OPERATION_KEY},
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "extra_forbidden"
    assert control_plane.create_calls == []


def test_abandon_is_restricted_to_the_original_create_operation(monkeypatch):
    app, control_plane, _, _ = _app(monkeypatch)
    response = TestClient(app).post(
        "/api/v1/projects/project-1/initialization/abandon",
        headers={"Idempotency-Key": OPERATION_KEY},
        json={},
    )

    assert response.status_code == 202
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json()["data"] == {
        "project_id": "project-1",
        "deletion_job_id": "job-1",
        "status": "pending",
    }
    assert control_plane.abandon_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
        }
    ]


def test_ordinary_delete_returns_accepted_cleanup_job_without_storage_details(monkeypatch):
    app, control_plane, _, _ = _app(monkeypatch)
    response = TestClient(app).delete("/api/v1/projects/project-1")

    assert response.status_code == 202
    assert response.json()["data"] == {
        "project_id": "project-1",
        "deletion_job_id": "job-1",
        "status": "pending",
    }
    assert "prefix" not in response.text
    assert response.json()["message"] == "Project deletion accepted"
    assert control_plane.delete_calls == [{"project_id": "project-1", "actor_user_id": "user-1"}]


def test_abandon_replay_reports_completed_instead_of_accepted(monkeypatch):
    control_plane = ControlPlaneStub()
    control_plane.abandon_result = ProjectDeletionResult(
        project_id="project-1",
        deletion_job_id="job-1",
        status="completed",
        replayed=True,
    )
    app, _, _, _ = _app(monkeypatch, control_plane)
    response = TestClient(app).post(
        "/api/v1/projects/project-1/initialization/abandon",
        headers={"Idempotency-Key": OPERATION_KEY},
        json={},
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["data"]["status"] == "completed"
    assert response.json()["message"] == "Project deletion completed"
