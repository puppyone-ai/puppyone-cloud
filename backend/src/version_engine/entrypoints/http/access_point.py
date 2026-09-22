"""
Access Point — credential resolution for scoped server access.

An Access Point is a credential (access key) that binds a client to
exactly one project + one scope (path / exclude / mode). The Git
adapter, the AP-FS HTTP API, and other protocol entry points all
resolve an incoming access key through ``resolve_access_point`` before
the engine sees any write intent.

URL surfaces that use access points are owned by the protocol adapters
themselves (see ``adapters/git/router.py`` for Git smart-HTTP and
``routers/access_point_fs.py`` for the FS CLI backend). This module no
longer mounts the old custom wire-protocol surface; access point traffic now
enters through Git smart HTTP or AP-FS.

An access key resolves through ``access_surface_credentials`` and an active
CLI Access Surface to one exact ``repository_scopes`` target. Credential
identity, Surface identity, and Scope geometry remain separate facts.
"""

from __future__ import annotations

import copy
import threading
import time

from fastapi import HTTPException

from src.exceptions import ErrorCode
from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import ResolvedRepositoryView, ScopeTarget
from src.repo.models import ResolvedScopeCredential
from src.utils.logger import log_error
from src.version_engine.adapters.git.protocol import ACCESS_POINT_MAIN_REF
from src.version_engine.infrastructure.supabase.scope_repository import (
    resolve_scope_access_credential,
)

_ACCESS_POINT_CACHE_TTL_SECONDS = 5.0
_access_point_cache: dict[str, tuple[float, str, dict]] = {}
_access_point_cache_lock = threading.Lock()


def _clone_auth_context(auth: dict) -> dict:
    return copy.deepcopy(auth)


def _get_cached_access_point(access_key: str) -> tuple[str, dict] | None:
    now = time.monotonic()
    with _access_point_cache_lock:
        cached = _access_point_cache.get(access_key)
        if cached is None:
            return None
        expires_at, project_id, auth = cached
        if expires_at <= now:
            _access_point_cache.pop(access_key, None)
            return None
        return project_id, _clone_auth_context(auth)


def _set_cached_access_point(access_key: str, project_id: str, auth: dict) -> None:
    with _access_point_cache_lock:
        _access_point_cache[access_key] = (
            time.monotonic() + _ACCESS_POINT_CACHE_TTL_SECONDS,
            project_id,
            _clone_auth_context(auth),
        )


def _auth_context_from_scope_credential(
    resolved: ResolvedScopeCredential,
) -> tuple[str, dict]:
    """Materialize a typed RuntimeGrant from resolved credential facts."""

    scope = resolved.scope
    project_id = scope.project_id
    scope_id = scope.id
    mode = RuntimeMode(scope.max_mode)
    target = ScopeTarget(project_id=project_id, scope_id=scope_id)
    runtime_grant = RuntimeGrant(
        principal=RuntimePrincipal(
            principal_id=resolved.credential_id,
            credential_kind=resolved.credential_type,
        ),
        target=target,
        repository_view=ResolvedRepositoryView(
            target=target,
            path_prefix=scope.path,
            excludes=tuple(scope.exclude),
            max_mode=mode.value,
            ref=ACCESS_POINT_MAIN_REF,
        ),
        mode=mode,
    )
    return project_id, {
        "agent": f"scope:{scope_id}",
        "_runtime_grant": runtime_grant,
        "_credential_id": resolved.credential_id,
        "_access_surface_id": resolved.access_surface_id,
        "_repo_facade": {
            "id": scope_id,
            "kind": "access_point",
            "ref": ACCESS_POINT_MAIN_REF,
            "object_store_scope": "project-shared",
        },
        "_user_identity": "",
    }


def resolve_access_point(access_key: str) -> tuple[str, dict]:
    """Resolve an access_key to (project_id, auth_context).

    The bounded legacy route still resolves through the canonical credential,
    Access Surface, and Scope repositories. No config or Scope-column fallback
    exists.

    Raises:
        HTTPException 401 if key is invalid / revoked / unknown.
    """
    cached = _get_cached_access_point(access_key)
    if cached is not None:
        return cached

    from src.infra.supabase.client import SupabaseClient

    try:
        resolved = resolve_scope_access_credential(SupabaseClient(), access_key)
    except Exception as e:
        log_error(f"[AP] credential target lookup error: {type(e).__name__}")
        raise HTTPException(
            status_code=503,
            detail="Repository view is temporarily unavailable",
            headers={
                "X-PuppyOne-Error-Code": str(
                    ErrorCode.REPOSITORY_STORAGE_UNAVAILABLE.value
                )
            },
        ) from e

    if resolved is None:
        raise HTTPException(status_code=401, detail="Invalid access point key")

    project_id, auth = _auth_context_from_scope_credential(resolved)
    _set_cached_access_point(access_key, project_id, auth)
    return project_id, auth
