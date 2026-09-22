"""Resolve Cloud Project context from Git and issue target-scoped credentials."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

import httpx
from postgrest.exceptions import APIError

from src.exceptions import (
    AppException,
    DatabaseSchemaOutdatedException,
    ErrorCode,
    NotFoundException,
    PermissionException,
    ServiceUnavailableException,
)
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService
from src.platform.idempotency import canonical_payload_hash, raise_idempotency_outcome
from src.platform.project.repository import ProjectRepositoryBase
from src.platform.repository_context.models import (
    GitCredentialMode,
    IssuedGitCredential,
    RepositoryProjectContext,
)
from src.platform.repository_context.repository import RepositoryContextRepository
from src.platform.repository_target.models import (
    ProjectRootTarget,
    RepositoryTarget,
    ScopeTarget,
)
from src.platform.repository_target.schemas import (
    RepositoryTargetSchema,
    repository_target_domain,
)
from src.repo.access_credentials import access_token_hash

_DEPENDENCY_READ_ATTEMPTS = 2
_logger = logging.getLogger("puppyone.repository_context")
_T = TypeVar("_T")


def _postgrest_error_code(error: APIError) -> str:
    """Read only the stable PostgREST code across client-library versions."""

    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    if error.args and isinstance(error.args[0], dict):
        raw = error.args[0].get("code")
        return raw if isinstance(raw, str) else ""
    return ""


class RepositoryContextService:
    def __init__(
        self,
        repository: RepositoryContextRepository,
        authorization: AuthorizationService,
        project_repository: ProjectRepositoryBase,
    ):
        self._repository = repository
        self._authorization = authorization
        self._project_repository = project_repository

    @staticmethod
    def _run_dependency(
        operation: str,
        callback: Callable[[], _T],
        *,
        attempts: int,
    ) -> _T:
        last_error: httpx.TransportError | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                return callback()
            except APIError as exc:
                # PGRST202 means PostgREST cannot find the required RPC in its
                # schema cache. During a rolling deploy that is a temporary
                # service-version mismatch, not a bad credential. Never relay
                # the raw error because it includes function names/signatures.
                if _postgrest_error_code(exc) == "PGRST202":
                    _logger.error(
                        "repository_context_database_schema_outdated",
                        extra={"operation": operation, "postgrest_code": "PGRST202"},
                    )
                    raise DatabaseSchemaOutdatedException() from exc
                raise
            except httpx.TransportError as exc:
                last_error = exc
                _logger.warning(
                    "repository_context_storage_transport_failure",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "attempts": max(1, attempts),
                        "error_type": type(exc).__name__,
                    },
                )
        assert last_error is not None
        raise ServiceUnavailableException(
            "Cloud repository context is temporarily unavailable"
        ) from last_error

    @classmethod
    def _read_dependency(cls, operation: str, callback: Callable[[], _T]) -> _T:
        return cls._run_dependency(operation, callback, attempts=_DEPENDENCY_READ_ATTEMPTS)

    @classmethod
    def _write_dependency(cls, operation: str, callback: Callable[[], _T]) -> _T:
        return cls._run_dependency(operation, callback, attempts=1)

    def _scope_for_target(self, target: RepositoryTarget, *, operation: str) -> dict | None:
        if isinstance(target, ProjectRootTarget):
            return None
        scope = self._read_dependency(
            operation,
            lambda: self._repository.get_scope(target.project_id, target.scope_id),
        )
        if scope is None:
            return None
        if (
            str(scope.get("id") or "") != target.scope_id
            or str(scope.get("project_id") or "") != target.project_id
        ):
            return None
        return scope

    def issue_git_credential(
        self,
        project_id: str,
        user_id: str,
        operation_key: str,
        target_schema: RepositoryTargetSchema,
        mode: GitCredentialMode,
        raw_token: str,
    ) -> IssuedGitCredential:
        target = repository_target_domain(target_schema)
        if target.project_id != project_id:
            raise AppException(
                code=ErrorCode.TARGET_KIND_MISMATCH,
                status_code=422,
                message="Git credential target Project mismatch",
            )
        action = (
            ProjectAction.CONTENT_WRITE
            if mode is GitCredentialMode.READ_WRITE
            else ProjectAction.CONTENT_READ
        )
        grant = self._authorization.authorize(project_id, user_id, action)
        if isinstance(target, ScopeTarget):
            scope = self._scope_for_target(target, operation="scope.get_for_git_credential")
            if scope is None:
                raise NotFoundException(
                    "Requested repository Scope does not exist",
                    code=ErrorCode.SCOPE_NOT_FOUND,
                )
            if mode is GitCredentialMode.READ_WRITE and scope.get("max_mode") != "rw":
                raise PermissionException(
                    "Git credential mode cannot exceed Scope mode",
                    code=ErrorCode.FORBIDDEN,
                )
        credential_hash = access_token_hash(raw_token)
        payload_hash = canonical_payload_hash(
            {
                "credential_hash": credential_hash,
                "mode": mode.value,
                "project_id": project_id,
                "target": target_schema.model_dump(mode="json"),
            }
        )
        outcome = self._write_dependency(
            "git_credential.issue",
            lambda: self._repository.issue_user_git_credential(
                operation_key=operation_key,
                payload_hash=payload_hash,
                org_id=grant.org_id,
                target=target,
                user_id=user_id,
                mode=mode,
                raw_token=raw_token,
            ),
        )
        result = str(outcome.get("outcome") or "")
        if result in {"conflict", "gone", "invalid"}:
            raise_idempotency_outcome(result, resource="git_credential")
        if result not in {"created", "replayed"}:
            raise RuntimeError(f"Unexpected Git credential issue outcome: {result or 'missing'}")
        credential_id = str(outcome.get("credential_id") or "")
        if not credential_id:
            raise RuntimeError("Git credential issue outcome did not include a credential id")
        return IssuedGitCredential(
            credential_id=credential_id,
            target=target,
            mode=mode,
            replayed=result == "replayed",
        )

    def revoke_git_credential(
        self,
        project_id: str,
        user_id: str,
        credential_id: str,
    ) -> None:
        """Revoke one owned credential even after Project access is lost.

        Revocation is monotonic and the storage RPC matches all of credential,
        Project, and human owner. It must not depend on a still-current
        ProjectGrant.
        """
        revoked = self._write_dependency(
            "git_credential.revoke",
            lambda: self._repository.revoke_user_git_credential(
                credential_id=credential_id,
                project_id=project_id,
                user_id=user_id,
            ),
        )
        if not revoked:
            raise NotFoundException("Git credential not found", code=ErrorCode.NOT_FOUND)

    def get_repository_context(
        self,
        project_id: str,
        user_id: str,
        target_schema: RepositoryTargetSchema,
    ) -> RepositoryProjectContext:
        """Authorize and describe one Project-owned repository target.

        The caller parses its canonical Git URL locally. Cloud receives only
        normal resource identity and current human authentication; no local
        path, checkout, device, or remote URL crosses this boundary.
        """
        target = repository_target_domain(target_schema)
        if target.project_id != project_id:
            raise AppException(
                code=ErrorCode.TARGET_KIND_MISMATCH,
                status_code=422,
                message="Repository context target Project mismatch",
            )
        grant = self._authorization.authorize(
            project_id,
            user_id,
            ProjectAction.PROJECT_READ,
        )
        project = self._read_dependency(
            "project.get_for_repository_context",
            lambda: self._project_repository.get_by_id(project_id),
        )
        if project is None or project.org_id != grant.org_id:
            raise NotFoundException("Repository Project not found", code=ErrorCode.NOT_FOUND)

        if isinstance(target, ProjectRootTarget):
            scope_path = None
        else:
            scope = self._scope_for_target(
                target,
                operation="scope.get_for_repository_context",
            )
            if scope is None:
                raise NotFoundException(
                    "Repository Scope not found",
                    code=ErrorCode.SCOPE_NOT_FOUND,
                )
            scope_path = str(scope.get("path") or "").strip("/")
        if isinstance(target, ScopeTarget) and not scope_path:
            raise AppException(
                code=ErrorCode.TARGET_KIND_MISMATCH,
                status_code=422,
                message="A repository Scope must resolve to a non-root path",
            )

        return RepositoryProjectContext(
            project=project,
            grant=grant,
            target=target,
            scope_path=scope_path,
        )
