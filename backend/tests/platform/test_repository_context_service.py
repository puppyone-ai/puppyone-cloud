from __future__ import annotations

from datetime import UTC, datetime

import pytest
from postgrest.exceptions import APIError

from src.exceptions import (
    AppException,
    DatabaseSchemaOutdatedException,
    ErrorCode,
    NotFoundException,
    PermissionException,
)
from src.platform.authorization.models import (
    ROLE_CAPABILITIES,
    GrantSource,
    ProjectGrant,
    ProjectRole,
)
from src.platform.project.models import Project
from src.platform.repository_context.models import GitCredentialMode
from src.platform.repository_context.schemas import (
    GitCredentialIssueRequest,
    RepositoryContextResolveRequest,
)
from src.platform.repository_context.service import RepositoryContextService
from src.platform.repository_target.models import ProjectRootTarget, ScopeTarget

OPERATION_KEY = "123e4567-e89b-42d3-a456-426614174000"
RAW_CREDENTIAL = "pwg_" + "A" * 43


class AuthorizationStub:
    def __init__(self, role: ProjectRole):
        self.role = role

    def authorize(self, project_id, user_id, action):
        grant = ProjectGrant(
            project_id=project_id,
            org_id="org-1",
            user_id=user_id,
            role=self.role,
            source=GrantSource.PROJECT_MEMBER,
            capabilities=ROLE_CAPABILITIES[self.role],
        )
        if not grant.allows(action):
            raise PermissionException()
        return grant


class DenyingAuthorizationStub:
    def authorize(self, *_args, **_kwargs):
        raise PermissionException()


class RepositoryStub:
    def __init__(self, *, scope=None):
        self.scope = scope
        self.issued = []
        self.scope_reads = 0

    def get_scope(self, *_args):
        self.scope_reads += 1
        return self.scope

    def issue_user_git_credential(self, **kwargs):
        self.issued.append(kwargs)
        return {"outcome": "created", "credential_id": "credential-1"}

    def revoke_user_git_credential(self, **kwargs):
        self.revoked = kwargs
        return kwargs["credential_id"] == "credential-1"


class MissingCredentialRpcRepository(RepositoryStub):
    def issue_user_git_credential(self, **_kwargs):
        raise APIError(
            {
                "code": "PGRST202",
                "message": "Could not find a function in the schema cache",
                "details": "public.secret_rpc(private_signature)",
                "hint": "Reload the schema",
            }
        )


class ProjectRepositoryStub:
    def __init__(self, *, missing=False):
        now = datetime.now(UTC)
        self.project = (
            None
            if missing
            else Project(
                id="project-1",
                name="Project One",
                description="A test project",
                org_id="org-1",
                visibility="private",
                bound_git_branch="main",
                created_at=now,
                updated_at=now,
            )
        )

    def get_by_id(self, project_id):
        return self.project if self.project and self.project.id == project_id else None


def _service(repository, role=ProjectRole.EDITOR):
    return RepositoryContextService(
        repository,
        AuthorizationStub(role),
        ProjectRepositoryStub(),
    )


def _scope(*, scope_id="scope-child", project_id="project-1", mode="rw", path="docs"):
    return {
        "id": scope_id,
        "project_id": project_id,
        "max_mode": mode,
        "path": path,
    }


def _root_target(project_id="project-1"):
    return RepositoryContextResolveRequest(
        target={"kind": "project_root", "project_id": project_id}
    ).target


def _scope_target():
    return RepositoryContextResolveRequest(
        target={
            "kind": "scope",
            "project_id": "project-1",
            "scope_id": "scope-child",
        }
    ).target


def test_root_target_resolves_project_grant_without_checkout_identity():
    repository = RepositoryStub()
    context = _service(repository).get_repository_context("project-1", "user-1", _root_target())

    assert context.project.id == "project-1"
    assert context.grant.role is ProjectRole.EDITOR
    assert context.target == ProjectRootTarget(project_id="project-1")
    assert context.scope_path is None
    assert repository.scope_reads == 0


def test_scope_target_resolves_exact_repository_view():
    repository = RepositoryStub(scope=_scope(path="docs/private", mode="r"))
    context = _service(repository, ProjectRole.VIEWER).get_repository_context(
        "project-1", "user-1", _scope_target()
    )

    assert context.target == ScopeTarget(project_id="project-1", scope_id="scope-child")
    assert context.scope_path == "docs/private"


def test_repository_context_rejects_target_mismatch_missing_project_and_access():
    with pytest.raises(AppException) as caught:
        _service(RepositoryStub()).get_repository_context(
            "project-1", "user-1", _root_target("project-2")
        )
    assert caught.value.code is ErrorCode.TARGET_KIND_MISMATCH

    missing = RepositoryContextService(
        RepositoryStub(),
        AuthorizationStub(ProjectRole.ADMIN),
        ProjectRepositoryStub(missing=True),
    )
    with pytest.raises(NotFoundException, match="Project not found"):
        missing.get_repository_context("project-1", "user-1", _root_target())

    denied = RepositoryContextService(
        RepositoryStub(), DenyingAuthorizationStub(), ProjectRepositoryStub()
    )
    with pytest.raises(PermissionException):
        denied.get_repository_context("project-1", "user-1", _root_target())


def test_editor_issues_user_owned_root_git_credential():
    repository = RepositoryStub()
    payload = GitCredentialIssueRequest(
        target={"kind": "project_root", "project_id": "project-1"},
        mode=GitCredentialMode.READ_WRITE,
        credential=RAW_CREDENTIAL,
    )

    issued = _service(repository).issue_git_credential(
        "project-1", "user-1", OPERATION_KEY, payload.target, payload.mode, payload.credential
    )

    assert issued.target == ProjectRootTarget(project_id="project-1")
    assert issued.credential_id == "credential-1"
    assert repository.scope_reads == 0
    assert len(repository.issued) == 1
    request = repository.issued[0]
    assert request["operation_key"] == OPERATION_KEY
    assert len(request["payload_hash"]) == 64
    assert request["org_id"] == "org-1"
    assert request["target"] == ProjectRootTarget(project_id="project-1")
    assert request["user_id"] == "user-1"
    assert request["mode"] is GitCredentialMode.READ_WRITE
    assert request["raw_token"] == RAW_CREDENTIAL


def test_missing_git_credential_rpc_is_typed_retryable_service_upgrade():
    payload = GitCredentialIssueRequest(
        target={"kind": "project_root", "project_id": "project-1"},
        mode=GitCredentialMode.READ_WRITE,
        credential=RAW_CREDENTIAL,
    )

    with pytest.raises(DatabaseSchemaOutdatedException) as caught:
        _service(MissingCredentialRpcRepository()).issue_git_credential(
            "project-1",
            "user-1",
            OPERATION_KEY,
            payload.target,
            payload.mode,
            payload.credential,
        )

    error = caught.value
    assert error.status_code == 503
    assert error.details == {
        "code": "database_schema_outdated",
        "retryable": True,
    }
    assert error.headers == {"Retry-After": "30"}
    assert "function" not in error.message.lower()
    assert "signature" not in error.message.lower()


def test_viewer_may_issue_read_but_not_write_credential():
    repository = RepositoryStub()
    service = _service(repository, ProjectRole.VIEWER)
    read_target = GitCredentialIssueRequest(
        target={"kind": "project_root", "project_id": "project-1"},
        mode=GitCredentialMode.READ,
        credential=RAW_CREDENTIAL,
    )
    assert (
        service.issue_git_credential(
            "project-1", "user-1", OPERATION_KEY, read_target.target, read_target.mode,
            read_target.credential,
        ).mode
        is GitCredentialMode.READ
    )

    with pytest.raises(PermissionException):
        service.issue_git_credential(
            "project-1",
            "user-1",
            OPERATION_KEY,
            read_target.target,
            GitCredentialMode.READ_WRITE,
            read_target.credential,
        )


def test_scope_mode_and_project_id_bound_credential_exactly():
    service = _service(RepositoryStub(scope=_scope(mode="r")))
    scoped = GitCredentialIssueRequest(
        target={
            "kind": "scope",
            "project_id": "project-1",
            "scope_id": "scope-child",
        },
        mode=GitCredentialMode.READ_WRITE,
        credential=RAW_CREDENTIAL,
    )
    with pytest.raises(PermissionException, match="cannot exceed Scope mode"):
        service.issue_git_credential(
            "project-1", "user-1", OPERATION_KEY, scoped.target, scoped.mode, scoped.credential
        )

    mismatched = GitCredentialIssueRequest(
        target={"kind": "project_root", "project_id": "project-2"},
        mode=GitCredentialMode.READ,
        credential=RAW_CREDENTIAL,
    )
    with pytest.raises(AppException) as caught:
        service.issue_git_credential(
            "project-1", "user-1", OPERATION_KEY, mismatched.target,
            mismatched.mode, mismatched.credential,
        )
    assert caught.value.code is ErrorCode.TARGET_KIND_MISMATCH


def test_user_may_revoke_only_an_owned_project_git_credential():
    repository = RepositoryStub()
    service = RepositoryContextService(
        repository,
        DenyingAuthorizationStub(),
        ProjectRepositoryStub(),
    )

    service.revoke_git_credential("project-1", "user-1", "credential-1")
    assert repository.revoked == {
        "credential_id": "credential-1",
        "project_id": "project-1",
        "user_id": "user-1",
    }

    with pytest.raises(NotFoundException):
        service.revoke_git_credential("project-1", "user-1", "not-owned")
