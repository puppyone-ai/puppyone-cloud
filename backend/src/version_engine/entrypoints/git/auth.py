"""Git access-point and project credential resolution."""

from __future__ import annotations

import asyncio
import base64

from fastapi import HTTPException, Request

from src.config import settings
from src.infra.supabase.client import SupabaseClient
from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import (
    ProjectRootTarget,
    ResolvedRepositoryView,
    ScopeTarget,
)
from src.platform.repository_target.auth_context import repository_view_from_auth
from src.repo.access_credentials import AccessCredentialRepository
from src.repo.scope_repository import RepositoryScopeRepository
from src.version_engine.entrypoints.git.locator import validate_git_locator_id
from src.version_engine.entrypoints.http.access_point import resolve_access_point
from src.version_engine.admission.channel_pause import enforce_channel_pause
from src.version_engine.write_engine.path_utils import normalize_path


async def resolve_git_access_point(
    access_key: str,
    request: Request,
    *,
    resolver=None,
) -> tuple[str, dict]:
    # ``resolver`` preserves the router's long-standing test/integration seam
    # while keeping all legacy identity/pause checks in this one function.
    project_id, auth = await asyncio.to_thread(
        resolver or resolve_access_point,
        access_key,
    )
    bound_identity = auth.get("_user_identity", "")
    request_identity = request_actor(request, auth)
    if bound_identity and request_identity != bound_identity:
        raise HTTPException(
            status_code=401,
            detail="User identity mismatch: key is bound to a different user",
        )
    # Native Git clients do not reliably send custom headers, so infer the
    # Git Remote access surface from the route.
    enforce_channel_pause(auth, "git_remote", log_prefix="[GitAP]")
    return project_id, auth


async def resolve_git_project_auth(
    project_id: str,
    request: Request,
    requested_scope: str = "",
) -> dict:
    """Resolve the canonical Project-root Git route.

    ``requested_scope`` remains only as a compatibility guard for older
    internal callers. Query strings can never retarget a canonical locator.
    """
    if normalize_path(requested_scope):
        raise _git_unauthorized()
    return await resolve_canonical_git_auth(project_id, request)


async def resolve_git_scope_auth(
    project_id: str,
    scope_id: str,
    request: Request,
) -> dict:
    return await resolve_canonical_git_auth(project_id, request, scope_id=scope_id)


async def resolve_canonical_git_auth(
    project_id: str,
    request: Request,
    *,
    scope_id: str | None = None,
) -> dict:
    try:
        project_id = validate_git_locator_id(project_id, field="project_id")
        if scope_id is not None:
            scope_id = validate_git_locator_id(scope_id, field="scope_id")
    except ValueError:
        raise _git_unauthorized() from None

    # ASGI exposes the undecoded request target. Reject percent-encoded route
    # identity even when the framework would decode it to an otherwise valid
    # ID; canonical locators have exactly one textual representation.
    raw_path = getattr(request, "scope", {}).get("raw_path", b"")
    if isinstance(raw_path, str):
        raw_path = raw_path.encode("ascii", errors="ignore")
    if b"%" in raw_path:
        raise _git_unauthorized()

    header = request.headers.get("authorization", "")
    username, password = basic_auth_credentials(header)
    bearer_token = bearer_token_from_header(header)
    token = bearer_token or password
    if not token:
        raise _git_unauthorized()

    if settings.SKIP_AUTH:
        auth = await _mock_git_auth(project_id, scope_id, username)
        enforce_channel_pause(auth, "git_remote", log_prefix="[Git]")
        return auth

    user_identity = request.headers.get("x-puppyone-user", "") or username
    repository = AccessCredentialRepository(SupabaseClient().client)
    resolved = await asyncio.to_thread(
        repository.resolve_git_runtime_credential,
        token,
    )
    if not resolved:
        raise _git_unauthorized()
    try:
        resolved_project_id = _required_runtime_text(resolved, "project_id")
        credential_id = _required_runtime_text(resolved, "credential_id")
        access_surface_id = _required_runtime_text(resolved, "access_surface_id")
        target_kind = _required_runtime_text(resolved, "target_kind")
        resolved_scope_id = (
            validate_git_locator_id(str(resolved["scope_id"]), field="scope_id")
            if resolved.get("scope_id") is not None
            else None
        )
        path_prefix = normalize_path(str(resolved.get("path_prefix") or ""))
        raw_excludes = resolved.get("excludes") or []
        if not isinstance(raw_excludes, (list, tuple)) or not all(
            isinstance(item, str) for item in raw_excludes
        ):
            raise ValueError("excludes must be a string list")
        excludes = tuple(
            normalized
            for item in raw_excludes
            if (normalized := normalize_path(item))
        )
        target_max_mode = _required_runtime_text(resolved, "target_max_mode")
        mode = RuntimeMode(_required_runtime_text(resolved, "effective_mode"))
    except (KeyError, TypeError, ValueError):
        # A malformed or partially migrated authorization fact set is not a
        # transport error.  Fail closed with the same response as an unknown
        # credential/target so callers cannot probe database state.
        raise _git_unauthorized() from None

    if resolved_project_id != project_id:
        raise _git_unauthorized()
    if target_kind == "project_root":
        if scope_id is not None or resolved_scope_id is not None or path_prefix or excludes:
            raise _git_unauthorized()
        target = ProjectRootTarget(project_id=project_id)
    elif target_kind == "scope":
        if scope_id is None or resolved_scope_id != scope_id or not path_prefix:
            raise _git_unauthorized()
        target = ScopeTarget(project_id=project_id, scope_id=scope_id)
    else:
        raise _git_unauthorized()

    try:
        repository_view = ResolvedRepositoryView(
            target=target,
            path_prefix=path_prefix,
            excludes=excludes,
            max_mode=target_max_mode,
        )
    except ValueError:
        raise _git_unauthorized() from None

    runtime_grant = RuntimeGrant(
        principal=RuntimePrincipal(
            principal_id=credential_id,
            credential_kind="git_http_token",
        ),
        target=target,
        repository_view=repository_view,
        mode=mode,
    )
    auth = {
        "agent": f"git-credential:{credential_id}",
        "_runtime_grant": runtime_grant,
        "_user_identity": user_identity,
        "_credential_id": credential_id,
        "_access_surface_id": access_surface_id,
        "_credential_user_id": resolved.get("user_id"),
    }
    # Native Git clients do not reliably send custom headers, so infer the
    # Git Remote access surface from the route.
    enforce_channel_pause(auth, "git_remote", log_prefix="[Git]")
    return auth


def _required_runtime_text(resolved: dict, field: str) -> str:
    value = resolved[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _git_unauthorized() -> HTTPException:
    # Keep route/project/scope/credential mismatches indistinguishable.
    return HTTPException(
        status_code=401,
        detail="Invalid Git credentials",
        headers={"WWW-Authenticate": 'Basic realm="PuppyOne Git"'},
    )


async def _mock_git_auth(
    project_id: str,
    scope_id: str | None,
    username: str,
) -> dict:
    """Bypass credential auth without bypassing canonical target geometry."""

    if scope_id is None:
        rows = await asyncio.to_thread(
            lambda: (
                SupabaseClient().client.table("projects")
                .select("id")
                .eq("id", project_id)
                .limit(1)
                .execute()
            ).data or []
        )
        if not rows:
            raise _git_unauthorized()
        target = ProjectRootTarget(project_id=project_id)
        view = ResolvedRepositoryView(
            target=target,
            path_prefix="",
            excludes=(),
            max_mode="rw",
        )
        mode = RuntimeMode.READ_WRITE
    else:
        scope = await asyncio.to_thread(
            RepositoryScopeRepository(SupabaseClient().client).get,
            scope_id,
        )
        if scope is None or scope.project_id != project_id:
            raise _git_unauthorized()
        path = normalize_path(scope.path)
        if not path:
            raise _git_unauthorized()
        try:
            mode = RuntimeMode(scope.max_mode)
        except ValueError:
            raise _git_unauthorized() from None
        configured_excludes = tuple(
            normalized
            for item in scope.exclude
            if (normalized := normalize_path(item))
        )
        all_scopes = await asyncio.to_thread(
            RepositoryScopeRepository(SupabaseClient().client).list_by_project,
            project_id,
        )
        descendant_excludes = tuple(
            child_path
            for child in all_scopes
            if child.id != scope.id
            and (child_path := normalize_path(child.path)).startswith(path + "/")
        )
        excludes = tuple(
            dict.fromkeys(configured_excludes + descendant_excludes)
        )
        target = ScopeTarget(project_id=project_id, scope_id=scope.id)
        view = ResolvedRepositoryView(
            target=target,
            path_prefix=path,
            excludes=excludes,
            max_mode=scope.max_mode,
        )
    runtime_grant = RuntimeGrant(
        principal=RuntimePrincipal(
            principal_id="test-git-credential",
            credential_kind="git_http_token",
        ),
        target=target,
        repository_view=view,
        mode=mode,
    )
    return {
        "agent": "git-credential:test-git-credential",
        "_runtime_grant": runtime_grant,
        "_user_identity": username,
    }


def scope_path_for_auth(auth: dict) -> str:
    return normalize_path(repository_view_from_auth(auth).path_prefix)


def scope_excludes_for_auth(auth: dict) -> list[str]:
    return list(repository_view_from_auth(auth).excludes)


def request_actor(request: Request, auth: dict) -> str:
    return (
        request.headers.get("x-puppyone-user")
        or request.headers.get("x-git-actor")
        or basic_auth_username(request.headers.get("authorization", ""))
        or auth.get("agent")
        or "git"
    )


def basic_auth_username(header: str) -> str:
    username, _password = basic_auth_credentials(header)
    return username


def basic_auth_credentials(header: str) -> tuple[str, str]:
    if not header.lower().startswith("basic "):
        return "", ""
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return "", ""
    username, _, password = decoded.partition(":")
    return username, password


def bearer_token_from_header(header: str) -> str:
    if not header.lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()
