from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from src.platform.project.dependencies import get_project_repository
from src.platform.project.models import Project
from src.platform.repository_context.dependencies import get_repository_context_service
from src.platform.repository_context.models import (
    GitCredentialMode,
    IssuedGitCredential,
    RepositoryProjectContext,
)
from src.platform.repository_context.router import router
from src.platform.repository_target.models import ProjectRootTarget, ScopeTarget

_OPERATION_KEY = "123e4567-e89b-42d3-a456-426614174000"
_RAW_CREDENTIAL = "pwg_" + "A" * 43
_CONTRACT_HEADERS = {
    "X-PuppyOne-Repository-Contract": "2",
    "Idempotency-Key": _OPERATION_KEY,
}


def _project():
    now = datetime.now(UTC)
    return Project(
        id="project-1",
        name="Project One",
        description="Secret-free metadata",
        org_id="org-1",
        visibility="private",
        bound_git_branch="main",
        created_at=now,
        updated_at=now,
    )


def _grant():
    return ProjectGrant(
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        role=ProjectRole.EDITOR,
        source=GrantSource.PROJECT_MEMBER,
        capabilities=ROLE_CAPABILITIES[ProjectRole.EDITOR],
    )


class ServiceStub:
    def get_repository_context(self, project_id, user_id, target):
        assert project_id == "project-1" and user_id == "user-1"
        assert target.scope_id == "scope-child"
        return RepositoryProjectContext(
            project=_project(),
            grant=_grant(),
            target=ScopeTarget(project_id="project-1", scope_id="scope-child"),
            scope_path="docs/private",
        )

    def issue_git_credential(self, project_id, user_id, operation_key, target, mode, credential):
        assert project_id == "project-1" and user_id == "user-1"
        assert operation_key == _OPERATION_KEY
        assert credential == _RAW_CREDENTIAL
        return IssuedGitCredential(
            credential_id="credential-1",
            target=ProjectRootTarget(project_id="project-1"),
            mode=mode,
        )

    def revoke_git_credential(self, project_id, user_id, credential_id):
        assert (project_id, user_id, credential_id) == ("project-1", "user-1", "credential-1")


class AuthorizationStub:
    def authorize(self, project_id, user_id, _action):
        assert project_id == "project-1" and user_id == "user-1"
        return _grant()


class ProjectRepositoryStub:
    def get_by_id(self, project_id):
        return _project() if project_id == "project-1" else None


class ReplayedServiceStub(ServiceStub):
    def issue_git_credential(self, project_id, user_id, operation_key, target, mode, credential):
        issued = super().issue_git_credential(
            project_id, user_id, operation_key, target, mode, credential
        )
        return IssuedGitCredential(
            credential_id=issued.credential_id,
            target=issued.target,
            mode=issued.mode,
            replayed=True,
        )


def _app(service=ServiceStub):
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-1", email="user@example.com", role="authenticated"
    )
    app.dependency_overrides[get_repository_context_service] = service
    app.dependency_overrides[get_authorization_service] = AuthorizationStub
    app.dependency_overrides[get_project_repository] = ProjectRepositoryStub
    return app


def test_repository_context_route_returns_secret_free_project_context():
    response = TestClient(_app()).post(
        "/api/v1/projects/project-1/repository-context",
        headers=_CONTRACT_HEADERS,
        json={
            "target": {
                "kind": "scope",
                "project_id": "project-1",
                "scope_id": "scope-child",
            }
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["target"] == {
        "kind": "scope",
        "project_id": "project-1",
        "scope_id": "scope-child",
    }
    assert data["project"]["effective_role"] == "editor"
    assert data["scope_path"] == "docs/private"
    assert "credential" not in response.text.lower()
    assert "workspace_instance" not in response.text.lower()


def test_git_credential_route_never_echoes_client_generated_secret():
    response = TestClient(_app()).post(
        "/api/v1/projects/project-1/git-credentials",
        headers=_CONTRACT_HEADERS,
        json={
            "target": {"kind": "project_root", "project_id": "project-1"},
            "mode": GitCredentialMode.READ_WRITE.value,
            "credential": _RAW_CREDENTIAL,
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert "credential" not in data
    assert _RAW_CREDENTIAL not in response.text
    assert response.headers["Idempotency-Replayed"] == "false"
    assert data["remote"]["target"] == {
        "kind": "project_root",
        "project_id": "project-1",
    }
    assert data["remote"]["username"] == "x-puppyone-token"


def test_git_credential_exact_replay_returns_original_id_with_replay_header():
    response = TestClient(_app(ReplayedServiceStub)).post(
        "/api/v1/projects/project-1/git-credentials",
        headers=_CONTRACT_HEADERS,
        json={
            "target": {"kind": "project_root", "project_id": "project-1"},
            "mode": GitCredentialMode.READ_WRITE.value,
            "credential": _RAW_CREDENTIAL,
        },
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["data"]["id"] == "credential-1"
    assert "credential" not in response.json()["data"]


def test_git_credential_issue_requires_a_canonical_operation_key():
    body = {
        "target": {"kind": "project_root", "project_id": "project-1"},
        "mode": GitCredentialMode.READ_WRITE.value,
        "credential": _RAW_CREDENTIAL,
    }
    missing = TestClient(_app()).post(
        "/api/v1/projects/project-1/git-credentials",
        headers={"X-PuppyOne-Repository-Contract": "2"},
        json=body,
    )
    invalid = TestClient(_app()).post(
        "/api/v1/projects/project-1/git-credentials",
        headers={
            "X-PuppyOne-Repository-Contract": "2",
            "Idempotency-Key": "not-a-uuid",
        },
        json=body,
    )

    assert missing.status_code == 400
    assert missing.json()["data"]["code"] == "idempotency_key_required"
    assert invalid.status_code == 422
    assert invalid.json()["data"]["code"] == "idempotency_key_invalid"


def test_git_credential_route_revokes_only_the_named_user_credential():
    response = TestClient(_app()).delete(
        "/api/v1/projects/project-1/git-credentials/credential-1",
        headers=_CONTRACT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"id": "credential-1", "revoked": True}
