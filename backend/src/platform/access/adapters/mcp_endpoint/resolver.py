"""Resolve every MCP bearer key into the canonical scoped-FS runtime.

Authentication is deliberately performed here, once, against
``access_surface_credentials``.  Neither the MCP transport nor the individual
agent/endpoint connectors implement a second key lookup path.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from src.exceptions import ErrorCode
from src.infra.supabase.dependencies import get_supabase_client
from src.platform.access.credential_repository import AccessCredentialRepository
from src.platform.access.filesystem.context import ScopedFsContext
from src.platform.access.filesystem.policy import resolve_mcp_fs_allowed_tools
from src.platform.access.surface_repository import AccessSurfaceRepository
from src.platform.authorization.models import ProjectAction
from src.platform.repository_target.scope_repository import RepositoryScopeRepository


@dataclass(frozen=True)
class ResolvedMcpRuntime:
    context: ScopedFsContext
    surface_kind: str
    surface: dict


def _list_project_scopes(project_id: str) -> list[dict]:
    """All scope rows (path only) for the project — input for carved-exclude
    computation. Storage failures are authorization failures, never an empty
    topology, because dropping child exclusions would widen the runtime view."""
    try:
        return RepositoryScopeRepository(get_supabase_client()).list_paths_by_project(project_id)
    except Exception as exc:  # storage failures fail closed at the protocol boundary
        raise HTTPException(
            status_code=503,
            detail="Repository view could not be resolved",
            headers={"X-PuppyOne-Error-Code": str(ErrorCode.REPOSITORY_STORAGE_UNAVAILABLE.value)},
        ) from exc


def _merge_scope_excludes(user_excludes, scope_path: str, all_scopes: list[dict]) -> list[str]:
    """Merge user-configured excludes with carved child-scope paths (GAP-4).

    An MCP Scope must hide its declared child Scopes — the same isolation
    the git/CLI admission path applies via ``compute_carved_excludes``. Without
    this an MCP key bound to a parent scope could ``ls``/``cat``/``grep`` into its
    child Scope subtrees. The Project-root projection (``scope_path == ""``)
    intentionally carves nothing.
    """
    from src.version_engine.admission.repo_facade import compute_carved_excludes

    carved = list(compute_carved_excludes(scope_path, all_scopes))
    return list(dict.fromkeys(list(user_excludes) + carved))


def _resolve_surface(key: str) -> tuple[dict, dict]:
    """Return the authenticated surface and its provider-specific policy."""

    sb = get_supabase_client()
    credential = AccessCredentialRepository(sb).get_active_by_token(key)
    if not credential:
        raise HTTPException(status_code=404, detail="MCP access surface not found")

    surface = AccessSurfaceRepository(sb).get(credential["access_surface_id"])
    if not surface or surface.get("kind") not in {"agent", "mcp"}:
        raise HTTPException(status_code=404, detail="MCP access surface not found")
    if surface.get("status") != "active":
        raise HTTPException(status_code=403, detail="MCP access surface is not active")

    policy: dict = {}
    if surface.get("kind") == "mcp":
        policy_resp = (
            sb.table("access_surface_policies")
            .select("*")
            .eq("access_surface_id", surface["id"])
            .limit(1)
            .execute()
        )
        policy_rows = policy_resp.data or []
        policy = policy_rows[0] if policy_rows else {}
    return surface, policy


def _owner_grant(project_id: str, owner: str):
    """The endpoint's delegated authority: its owner's current Project grant.

    An MCP key is a shared credential issued by a Project member. It never
    outlives that member's access: removal denies it and a Viewer downgrade
    makes it read-only, exactly like the member's own Git credentials.
    Storage failures propagate as retryable 503s, never as an empty grant.
    """
    from src.platform.authorization.factory import build_authorization_service

    return build_authorization_service(get_supabase_client()).resolve_project_grant(
        project_id, owner
    )


def resolve_mcp_runtime(api_key: str) -> ResolvedMcpRuntime:
    key = (api_key or "").strip()
    if not key.startswith("mcp_"):
        raise HTTPException(status_code=401, detail="Invalid MCP API key")

    surface, policy = _resolve_surface(key)
    if surface.get("scope_id"):
        # A non-root Scope view needs the native repository Scope implementation.
        raise HTTPException(
            status_code=501,
            detail={
                "code": "native_scope_not_available",
                "message": "Scoped MCP filesystems require the new repository Scope implementation",
            },
        )

    project_id = surface["project_id"]
    owner = surface.get("created_by") or ""
    grant = _owner_grant(project_id, owner) if owner else None
    if grant is None or not grant.allows(ProjectAction.CONTENT_READ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "mcp_owner_access_revoked",
                "message": "The MCP endpoint's owner no longer has access to this Project",
            },
        )

    # The endpoint policy may narrow its owner's authority, never widen it.
    accesses = (policy.get("fs_policy") or {}).get("accesses") or []
    policy_writable = surface.get("kind") == "agent" or any(
        not bool(access.get("readonly", True)) for access in accesses
    )
    mode = "rw" if policy_writable and grant.allows(ProjectAction.CONTENT_WRITE) else "ro"
    context = ScopedFsContext(
        api_key=key,
        endpoint_id=surface["id"],
        endpoint_name=surface.get("name") or "MCP Access Surface",
        project_id=project_id,
        user_id=owner,
        scope_id="",
        scope_path="",
        mode=mode,
        exclude=[],
        allowed_tools=resolve_mcp_fs_allowed_tools(
            policy.get("tools_policy") if surface.get("kind") == "mcp" else None,
            writable=mode == "rw",
        ),
    )
    return ResolvedMcpRuntime(context=context, surface_kind=surface["kind"], surface=surface)


def resolve_mcp_scoped_fs_context(api_key: str) -> ScopedFsContext:
    """Compatibility facade for scoped-FS callers that need only context."""

    return resolve_mcp_runtime(api_key).context
