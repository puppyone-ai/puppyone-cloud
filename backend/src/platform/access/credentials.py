"""Git/CLI credential resolution and legacy Scope credential admission.

Access owns the shared credential repository. This resolver accepts the existing
Git HTTP credential type; MCP keeps its own bearer/surface admission path.
"""

from fastapi import HTTPException

from src.infra.supabase.client import SupabaseClient
from src.platform.access.models import ResolvedScopeCredential
from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import ResolvedRepositoryView


def resolve_access_point(access_key: str) -> tuple[str, dict]:
    from src.infra.supabase.client import SupabaseClient
    from src.platform.repository_target.models import ProjectRootTarget
    from src.platform.access.credential_repository import AccessCredentialRepository

    try:
        resolved = AccessCredentialRepository(
            SupabaseClient().client
        ).resolve_git_runtime_credential(access_key)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Repository credential lookup unavailable"
        ) from exc
    if not resolved:
        raise HTTPException(status_code=401, detail="Invalid repository credential")
    if resolved.get("target_kind") != "project_root" or resolved.get("scope_id") is not None:
        raise HTTPException(status_code=501, detail={"code": "native_scope_not_available"})
    if resolved.get("path_prefix") or resolved.get("excludes"):
        raise HTTPException(status_code=403, detail="Invalid Project-root view")
    project_id = resolved["project_id"]
    target = ProjectRootTarget(project_id=project_id)
    grant = RuntimeGrant(
        principal=RuntimePrincipal(
            principal_id=resolved["credential_id"], credential_kind="git_http_token"
        ),
        target=target,
        repository_view=ResolvedRepositoryView(
            target=target, path_prefix="", excludes=(), max_mode=resolved["target_max_mode"]
        ),
        mode=RuntimeMode(resolved["effective_mode"]),
    )
    return project_id, {
        "agent": "git-credential:" + resolved["credential_id"],
        "_runtime_grant": grant,
        "_credential_id": resolved["credential_id"],
        "_access_surface_id": resolved["access_surface_id"],
        "_credential_user_id": resolved.get("user_id"),
        "_user_identity": "",
    }


def resolve_scope_access_credential(
    supabase: SupabaseClient,
    access_key: str,
) -> ResolvedScopeCredential | None:
    """Resolve the existing Scope credential chain without owning its geometry.

    Storage failures propagate. Only an invalid credential/Surface/Scope chain
    returns None; callers retain their existing fail-closed admission policy.
    """
    from dataclasses import replace

    from src.platform.access.surface_repository import AccessSurfaceRepository
    from src.platform.repository_target.scope_repository import RepositoryScopeRepository

    credential = AccessSurfaceRepository(supabase.client).resolve_scope_credential(access_key)
    if credential is None:
        return None
    scope = RepositoryScopeRepository(supabase.client).get(credential.scope_id)
    if scope is None or scope.project_id != credential.project_id:
        return None
    if credential.mode_ceiling == "r" and scope.max_mode == "rw":
        scope = replace(scope, max_mode="r")
    return ResolvedScopeCredential(
        credential_id=credential.credential_id,
        credential_type=credential.credential_type,
        access_surface_id=credential.access_surface_id,
        scope=scope,
    )
