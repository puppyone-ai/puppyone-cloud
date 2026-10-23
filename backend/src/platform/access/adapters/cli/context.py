"""CLI AP-FS context: existing protocol behavior over admitted repository operations."""

from __future__ import annotations

import asyncio

from typing import Any

from fastapi import HTTPException

from src.exceptions import ErrorCode
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.access.credentials import resolve_access_point
from src.platform.repository_target.auth_context import repository_view_from_auth
from src.platform.repository_target.models import repository_target_scope_id
from src.version_engine.admission.channel_pause import enforce_channel_pause
from src.version_engine.admission.connector_policy import admit_cli_fs_command
from src.version_engine.admission.permission import (
    ensure_mode_writable,
    ensure_repo_readable,
    is_mode_writable,
)
from src.version_engine.admission.repo_facade import (
    RepositoryViewResolutionError,
    repo_facade_from_auth,
)
from src.version_engine.admission.validation import validate_limit, validate_path
from src.platform.access.filesystem.context import ScopedFsContext
from src.platform.access.filesystem.errors import ScopedFsError


def _is_not_found_error(exc: Exception) -> bool:
    """Normalize object-store not-found wrappers for the admin repair path."""

    message = str(exc).lower()
    return any(token in message for token in ("not found", "nosuchkey", "404", "does not exist"))


def _normalize_access_key(x_access_key: str | None) -> str:
    key = (x_access_key or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="X-Access-Key header is required")
    return key


async def _resolve_auth(
    x_access_key: str | None,
    x_puppyone_user: str | None,
    x_puppy_client: str | None = None,
    command: str | None = None,
) -> tuple[str, dict, dict]:
    project_id, auth = await asyncio.to_thread(
        resolve_access_point,
        _normalize_access_key(x_access_key),
        channel="cli",
    )

    bound_identity = auth.get("_user_identity", "")
    if bound_identity:
        if not x_puppyone_user:
            raise HTTPException(
                status_code=401,
                detail="X-PuppyOne-User header required: key is bound to a specific user",
            )
        if x_puppyone_user != bound_identity:
            raise HTTPException(
                status_code=401,
                detail="User identity mismatch: key is bound to a different user",
            )

    # Build scope_backend to compute carved_excludes (GAP-4 fix).
    # SupabaseClient() is a singleton so this does not open a new connection.
    from src.infra.supabase.client import SupabaseClient  # local import avoids circulars
    from src.version_engine.infrastructure.supabase.scope_repository import SupabaseScopeBackend

    scope_backend = SupabaseScopeBackend(SupabaseClient(), project_id)
    try:
        facade = repo_facade_from_auth(
            project_id, auth, kind="access_point", scope_backend=scope_backend
        )
    except RepositoryViewResolutionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Repository view is temporarily unavailable",
            headers={"X-PuppyOne-Error-Code": str(ErrorCode.REPOSITORY_STORAGE_UNAVAILABLE.value)},
        ) from exc
    view = repository_view_from_auth(auth)
    scope_path = validate_path(facade.scope_path)
    mode = facade.mode
    ensure_repo_readable(facade)

    # /ap-fs is the PuppyOne FS CLI command surface. It is governed by the
    # built-in CLI access surface.
    normalized_client = (x_puppy_client or "").strip().lower()
    if not normalized_client:
        raise HTTPException(
            status_code=400,
            detail="X-Puppy-Client header required",
        )
    if normalized_client != "cli":
        raise HTTPException(
            status_code=400,
            detail="AP-FS requires X-Puppy-Client: cli",
        )
    if command:
        admit_cli_fs_command(auth, command, normalized_client, log_prefix="[AP-FS]")
    else:
        enforce_channel_pause(auth, normalized_client, log_prefix="[AP-FS]")

    normalized_scope = {
        "id": repository_target_scope_id(view.target) or auth.get("agent"),
        "repo_id": facade.repo_id,
        "repo_kind": facade.kind,
        "repo_ref": facade.ref,
        "object_store_scope": facade.object_store_scope,
        "path": scope_path,
        "mode": mode,
        "exclude": list(facade.excludes),
    }
    return project_id, auth, normalized_scope


def _ensure_writable(scope: dict) -> None:
    ensure_mode_writable(str(scope.get("mode", "r")))


def _fs_error(
    status_code: int,
    error_code: str,
    message: str,
    *,
    path: str | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
    }
    if path is not None:
        detail["path"] = path
    return HTTPException(status_code=status_code, detail=detail)


def _clean_relative(path: str | None) -> str:
    raw = (path or "").strip()
    if raw in ("", "/", "."):
        return ""
    return validate_path(raw)


def _join_scope(scope_path: str, relative_path: str) -> str:
    if not scope_path:
        return relative_path
    if not relative_path:
        return scope_path
    return f"{scope_path}/{relative_path}"


def _relative_to_scope(full_path: str, scope_path: str) -> str:
    clean = full_path.strip("/")
    scope = scope_path.strip("/")
    if not scope:
        return clean
    if clean == scope:
        return ""
    prefix = f"{scope}/"
    if clean.startswith(prefix):
        return clean[len(prefix) :]
    return clean


def _matches_exclude(relative_path: str, excludes: list[Any]) -> bool:
    rel = relative_path.strip("/")
    if not rel:
        return False
    segments = rel.split("/")
    for item in excludes:
        pattern = str(item).strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if rel == pattern or rel.startswith(f"{pattern}/"):
                return True
        elif pattern in segments:
            return True
    return False


def _assert_not_excluded(relative_path: str, scope: dict) -> None:
    if _matches_exclude(relative_path, scope.get("exclude") or []):
        raise HTTPException(
            status_code=403, detail=f"Path is excluded from this access point: {relative_path}"
        )


def _entry_to_scoped_response(entry, scope: dict) -> dict:
    rel_path = _relative_to_scope(entry.path, scope["path"])
    return {
        "name": entry.name,
        "path": rel_path,
        "version_path": entry.path,
        "type": entry.type,
        "content_hash": entry.content_hash,
        "size_bytes": entry.size_bytes,
        "mime_type": entry.mime_type,
        "children_count": entry.children_count,
        "integrity_status": getattr(entry, "integrity_status", "ok"),
        "created_at": getattr(entry, "created_at", None),
        "modified_at": getattr(entry, "modified_at", None),
    }


def _is_hidden_path(path: str) -> bool:
    return any(part.startswith(".") for part in path.strip("/").split("/") if part)


def _filter_entries(entries: list, scope: dict, *, include_hidden: bool = False) -> list:
    filtered = []
    excludes = list(scope.get("exclude") or [])
    for entry in entries:
        rel_path = _relative_to_scope(entry.path, scope["path"])
        if not include_hidden and _is_hidden_path(rel_path):
            continue
        if _matches_exclude(rel_path, excludes):
            continue
        filtered.append(entry)
    return filtered


def _filter_directories(entries: list) -> list:
    return [entry for entry in entries if entry.type == "folder"]


def _scope_payload(scope: dict) -> dict:
    return {
        "id": scope.get("id", ""),
        "repo_id": scope.get("repo_id", ""),
        "repo_kind": scope.get("repo_kind", ""),
        "repo_ref": scope.get("repo_ref", "refs/heads/main"),
        "path": scope["path"],
        "mode": scope["mode"],
        "exclude": scope.get("exclude") or [],
    }


def _query_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _query_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) else default


def _query_optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _query_limited_int(value: Any, default: int, maximum: int) -> int:
    return validate_limit(_query_int(value, default), default=default, maximum=maximum)


def _operator(auth: dict) -> str:
    return f"access_point:{auth.get('agent', 'unknown')}"


def _scoped_fs_context(
    *,
    access_key: str | None,
    project_id: str,
    auth: dict,
    scope: dict,
) -> ScopedFsContext:
    scope_id = str(scope.get("id") or auth.get("agent") or "")
    return ScopedFsContext(
        api_key=(access_key or "").strip(),
        endpoint_id=scope_id,
        endpoint_name=str(auth.get("name") or scope_id),
        project_id=project_id,
        user_id=_operator(auth),
        scope_id=scope_id,
        scope_path=str(scope.get("path") or ""),
        mode="rw" if is_mode_writable(str(scope.get("mode", "r"))) else "ro",
        exclude=list(scope.get("exclude") or []),
        allowed_tools=None,
        channel="cli",
    )


def _scoped_fs_error(exc: ScopedFsError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _ops_stat(
    ops: ProductOperationAdapter,
    project_id: str,
    scope: dict,
    rel_path: str,
    *,
    include_size: bool = False,
):
    scoped = getattr(ops, "stat_in_scope", None)
    if scoped is not None:
        return scoped(
            project_id,
            scope["path"],
            rel_path,
            include_size=include_size,
        )
    return ops.stat(
        project_id,
        _join_scope(scope["path"], rel_path),
        include_size=include_size,
    )


def _ops_list_dir(
    ops: ProductOperationAdapter,
    project_id: str,
    scope: dict,
    rel_path: str,
    *,
    include_size: bool = False,
):
    scoped = getattr(ops, "list_dir_in_scope", None)
    if scoped is not None:
        return scoped(
            project_id,
            scope["path"],
            rel_path,
            include_size=include_size,
        )
    return ops.list_dir(
        project_id,
        _join_scope(scope["path"], rel_path),
        include_size=include_size,
    )


def _ops_list_tree(
    ops: ProductOperationAdapter,
    project_id: str,
    scope: dict,
    rel_path: str,
    max_depth: int,
    *,
    include_size: bool = False,
    max_entries: int | None = None,
):
    scoped = getattr(ops, "list_tree_in_scope", None)
    if scoped is not None:
        return scoped(
            project_id,
            scope["path"],
            rel_path,
            max_depth=max_depth,
            include_size=include_size,
            max_entries=max_entries,
        )
    return ops.list_tree(
        project_id,
        _join_scope(scope["path"], rel_path),
        max_depth=max_depth,
        include_size=include_size,
        max_entries=max_entries,
    )


def _ops_read_file(ops: ProductOperationAdapter, project_id: str, scope: dict, rel_path: str):
    scoped = getattr(ops, "read_file_in_scope", None)
    if scoped is not None:
        return scoped(project_id, scope["path"], rel_path)
    return ops.read_file(project_id, _join_scope(scope["path"], rel_path))


def _ops_read_file_range(
    ops: ProductOperationAdapter,
    project_id: str,
    scope: dict,
    rel_path: str,
    *,
    start: int = 0,
    limit: int | None = None,
):
    scoped = getattr(ops, "read_file_range_in_scope", None)
    if scoped is not None:
        return scoped(
            project_id,
            scope["path"],
            rel_path,
            start=start,
            limit=limit,
        )
    return ops.read_file_range(
        project_id,
        _join_scope(scope["path"], rel_path),
        start=start,
        limit=limit,
    )


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]
