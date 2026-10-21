"""CLI AP-FS write: existing protocol behavior over admitted repository operations."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from src.common_schemas import ApiResponse
from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.file_operations.upload_policy import (
    PER_FILE_MAX_BYTES as POLICY_PER_FILE_MAX_BYTES,
)
from src.file_operations.upload_policy import (
    path_has_blocked_segment,
)
from src.platform.access.repository_schemas import (
    CopyRequest,
    MkdirRequest,
    MoveRequest,
    RemoveRequest,
    RmdirRequest,
    TouchRequest,
    WriteFileRequest,
)
from src.version_engine.bootstrap.dependencies import get_version_write_command_service
from src.version_engine.write_engine.errors import (
    ConcurrentMutationError,
    NativeRevisionConflictError,
)

from .context import (
    _assert_not_excluded,
    _basename,
    _clean_relative,
    _ensure_writable,
    _fs_error,
    _join_scope,
    _operator,
    _ops_list_dir,
    _ops_stat,
    _resolve_auth,
    _scope_payload,
)

router = APIRouter()


def _upload_max_bytes_for_project(project_id: str) -> int:
    from src.platform.entitlements.service import EntitlementService
    from src.platform.project.repository import ProjectRepositorySupabase

    project = ProjectRepositorySupabase().get_by_id(project_id)
    if project is None:
        return POLICY_PER_FILE_MAX_BYTES
    limit = EntitlementService().enforced_limit_value(
        project.org_id,
        "upload.max_single_file_bytes",
    )
    return int(limit) if limit is not None else POLICY_PER_FILE_MAX_BYTES


def _assert_upload_policy(relative_path: str) -> None:
    """PUP-3 defense-in-depth: reject paths whose segments match the
    hardcoded blocklist (``.git``, ``node_modules``, …).

    Read-only operations don't call this — only the write endpoints
    (upload, write_file, mkdir, touch). The product contract is in
    ``docs/proposals/PUP-3-folder-upload-policy.md``.
    """
    is_blocked, seg = path_has_blocked_segment(relative_path)
    if is_blocked:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "policy_blocked",
                "segment": seg,
                "path": relative_path,
                "message": (
                    f"Path '{relative_path}' contains the blocked segment "
                    f"'{seg}' (PUP-3 folder-upload policy)."
                ),
            },
        )


def _destination_child_path(directory_rel: str, old_rel: str) -> str:
    child_name = _basename(old_rel)
    clean_dir = directory_rel.rstrip("/")
    return f"{clean_dir}/{child_name}" if clean_dir else child_name


def _dirname(path: str) -> str:
    clean = path.strip("/")
    if not clean or "/" not in clean:
        return ""
    return clean.rsplit("/", 1)[0]


def _is_descendant_destination(old_rel: str, new_rel: str) -> bool:
    old_clean = old_rel.strip("/")
    new_clean = new_rel.strip("/")
    return bool(old_clean and new_clean.startswith(f"{old_clean}/"))


def _resolve_copy_move_destination(
    project_id: str,
    scope: dict,
    ops: ProductOperationAdapter,
    old_rel: str,
    new_rel: str,
    *,
    target_directory: bool,
    no_target_directory: bool,
) -> tuple[str, str, Any | None]:
    if target_directory and no_target_directory:
        raise HTTPException(
            status_code=400,
            detail="target_directory and no_target_directory cannot both be true",
        )

    new_full = _join_scope(scope["path"], new_rel)
    new_entry = _ops_stat(ops, project_id, scope, new_rel)

    if target_directory:
        if new_entry is None or new_entry.type != "folder":
            raise _fs_error(
                400,
                "NOT_A_DIRECTORY",
                f"Not a directory: {new_rel or '.'}",
                path=new_rel,
            )
        new_rel = _destination_child_path(new_rel, old_rel)
        _assert_not_excluded(new_rel, scope)
        new_full = _join_scope(scope["path"], new_rel)
        new_entry = _ops_stat(ops, project_id, scope, new_rel)
    elif new_entry and new_entry.type == "folder" and not no_target_directory:
        new_rel = _destination_child_path(new_rel, old_rel)
        _assert_not_excluded(new_rel, scope)
        new_full = _join_scope(scope["path"], new_rel)
        new_entry = _ops_stat(ops, project_id, scope, new_rel)

    return new_rel, new_full, new_entry


def _is_directory_empty(
    project_id: str, rel_path: str, scope: dict, ops: ProductOperationAdapter
) -> bool:
    return len(_ops_list_dir(ops, project_id, scope, rel_path)) == 0


def _rmdir_chain(
    project_id: str, rel_path: str, scope: dict, ops: ProductOperationAdapter, *, parents: bool
) -> list[str]:
    """Return deepest-first empty directory chain removable by rmdir."""
    entry = _ops_stat(ops, project_id, scope, rel_path)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No such file or directory: {rel_path}")
    if entry.type != "folder":
        raise HTTPException(status_code=400, detail=f"Not a directory: {rel_path}")
    if not _is_directory_empty(project_id, rel_path, scope, ops):
        raise HTTPException(status_code=400, detail=f"Directory not empty: {rel_path}")

    removable = [rel_path]
    if not parents:
        return removable

    child_rel = rel_path
    parent_rel = _dirname(rel_path)
    while parent_rel:
        _assert_not_excluded(parent_rel, scope)
        parent = _ops_stat(ops, project_id, scope, parent_rel)
        if parent is None or parent.type != "folder":
            break
        remaining = [
            e
            for e in _ops_list_dir(ops, project_id, scope, parent_rel)
            if e.path.strip("/") != _join_scope(scope["path"], child_rel).strip("/")
        ]
        if remaining:
            break
        removable.append(parent_rel)
        child_rel = parent_rel
        parent_rel = _dirname(parent_rel)

    return removable


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    request: Request,
    path: str = Query(..., description="Destination path relative to the access point scope"),
    base_commit_id: str | None = Query(None, description="Expected current scope head"),
    message: str = Query("", description="Commit message"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="upload",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_path = _clean_relative(path)
    if not rel_path:
        raise HTTPException(status_code=400, detail="File path is required")
    _assert_not_excluded(rel_path, scope)
    _assert_upload_policy(rel_path)

    per_file_max_bytes = await asyncio.to_thread(
        _upload_max_bytes_for_project,
        project_id,
    )
    request_headers = getattr(request, "headers", {})
    content_length_header = request_headers.get("content-length")
    if content_length_header:
        try:
            content_length = int(content_length_header)
        except ValueError:
            content_length = -1
        if content_length > per_file_max_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File body is {content_length} bytes; per-file cap is "
                    f"{per_file_max_bytes} bytes."
                ),
            )

    content = await request.body()
    if len(content) > per_file_max_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File body is {len(content)} bytes; per-file cap is {per_file_max_bytes} bytes."
            ),
        )
    full_path = _join_scope(scope["path"], rel_path)
    try:
        outcome = await commands.write_bytes(
            project_id,
            rel_path,
            content,
            actor=_operator(auth),
            scope=scope["path"],
            message=message or f"ap upload {rel_path}",
            base_commit_id=base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ApiResponse.success(
        data={
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "commit_id": result.commit_id,
            "size_bytes": outcome.size_bytes,
        }
    )


@router.post("/write", response_model=ApiResponse)
async def write_file(
    body: WriteFileRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="write",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_path = _clean_relative(body.path)
    if not rel_path:
        raise HTTPException(status_code=400, detail="File path is required")
    _assert_not_excluded(rel_path, scope)
    _assert_upload_policy(rel_path)

    try:
        outcome = await commands.write_file(
            project_id,
            rel_path,
            body.content,
            node_type=body.node_type,
            actor=_operator(auth),
            scope=scope["path"],
            message=body.message,
            default_message_prefix="ap write",
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    full_path = _join_scope(scope["path"], outcome.path)
    return ApiResponse.success(
        data={
            "path": outcome.path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "commit_id": result.commit_id,
            "merged": False,
            "conflicts": 0,
        }
    )


@router.post("/mkdir", response_model=ApiResponse)
async def mkdir(
    body: MkdirRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="mkdir",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_path = _clean_relative(body.path)
    if not rel_path:
        raise HTTPException(status_code=400, detail="Directory path is required")
    _assert_not_excluded(rel_path, scope)
    _assert_upload_policy(rel_path)

    full_path = _join_scope(scope["path"], rel_path)
    existing = _ops_stat(commands.ops, project_id, scope, rel_path)
    if existing is not None:
        if existing.type == "folder" and body.parents:
            return ApiResponse.success(
                data={
                    "path": rel_path,
                    "version_path": full_path,
                    "scope": _scope_payload(scope),
                    "commit_id": "",
                    "created": False,
                }
            )
        raise HTTPException(status_code=400, detail=f"File exists: {rel_path}")

    parent_rel = _dirname(rel_path)
    if parent_rel and not body.parents:
        parent = _ops_stat(commands.ops, project_id, scope, parent_rel)
        if parent is None:
            raise HTTPException(status_code=404, detail=f"No such file or directory: {parent_rel}")
        if parent.type != "folder":
            raise HTTPException(status_code=400, detail=f"Not a directory: {parent_rel}")

    try:
        outcome = await commands.mkdir(
            project_id,
            rel_path,
            actor=_operator(auth),
            scope=scope["path"],
            message=f"ap mkdir {rel_path}",
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ApiResponse.success(
        data={
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "commit_id": result.commit_id,
        }
    )


@router.post("/touch", response_model=ApiResponse)
async def touch(
    body: TouchRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="touch",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_paths = [_clean_relative(p) for p in (body.paths or [body.path])]
    rel_paths = [p for p in rel_paths if p]
    if not rel_paths:
        raise HTTPException(status_code=400, detail="File path is required")

    existing_files: list[str] = []
    missing_files: list[str] = []
    for rel_path in rel_paths:
        _assert_not_excluded(rel_path, scope)
        _assert_upload_policy(rel_path)
        existing = _ops_stat(commands.ops, project_id, scope, rel_path)
        if existing is not None:
            if existing.type == "folder":
                raise HTTPException(status_code=400, detail=f"Is a directory: {rel_path}")
            existing_files.append(rel_path)
        else:
            missing_files.append(rel_path)

    results_by_path: dict[str, dict] = {}
    base_used = False
    if existing_files:
        try:
            outcome = await commands.touch(
                project_id,
                existing_files,
                actor=_operator(auth),
                scope=scope["path"],
                message=(
                    f"ap touch {existing_files[0]}"
                    if len(existing_files) == 1
                    else f"ap touch {len(existing_files)} files"
                ),
                base_commit_id=body.base_commit_id,
                defer_projection=True,
                source_channel="access_cli",
            )
            result = outcome.result
            base_used = True
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except (ConcurrentMutationError, NativeRevisionConflictError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        for rel_path in existing_files:
            results_by_path[rel_path] = {
                "path": rel_path,
                "version_path": _join_scope(scope["path"], rel_path),
                "commit_id": result.commit_id,
                "created": False,
                "touched": True,
            }

    for index, rel_path in enumerate(missing_files):
        full_path = _join_scope(scope["path"], rel_path)
        try:
            outcome = await commands.write_bytes(
                project_id,
                rel_path,
                b"",
                actor=_operator(auth),
                scope=scope["path"],
                message=f"ap touch {rel_path}",
                base_commit_id=body.base_commit_id if index == 0 and not base_used else None,
                defer_projection=True,
                source_channel="access_cli",
            )
            result = outcome.result
        except (ConcurrentMutationError, NativeRevisionConflictError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        results_by_path[rel_path] = {
            "path": rel_path,
            "version_path": full_path,
            "commit_id": result.commit_id,
            "created": True,
            "touched": True,
        }

    results = [results_by_path[p] for p in rel_paths]

    data = {
        "paths": rel_paths,
        "results": results,
        "scope": _scope_payload(scope),
    }
    if len(results) == 1:
        data.update(results[0])
    return ApiResponse.success(data=data)


@router.post("/mv", response_model=ApiResponse)
async def move(
    body: MoveRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="mv",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    old_rel = _clean_relative(body.old_path)
    new_rel = _clean_relative(body.new_path)
    if not old_rel:
        raise HTTPException(status_code=400, detail="Both old_path and new_path are required")
    _assert_not_excluded(old_rel, scope)
    _assert_not_excluded(new_rel, scope)
    # PUP-3: block writes landing inside a blocklisted path. The source
    # side is intentionally permissive so users can move/copy legacy
    # content OUT of a blocked directory while cleaning up.
    _assert_upload_policy(new_rel)

    old_full = _join_scope(scope["path"], old_rel)
    old_entry = _ops_stat(commands.ops, project_id, scope, old_rel)
    if old_entry is None:
        raise _fs_error(404, "NOT_FOUND", f"Path not found: {old_rel}", path=old_rel)

    new_rel, new_full, new_entry = _resolve_copy_move_destination(
        project_id,
        scope,
        commands.ops,
        old_rel,
        new_rel,
        target_directory=body.target_directory,
        no_target_directory=body.no_target_directory,
    )
    if _is_descendant_destination(old_rel, new_rel):
        raise _fs_error(
            400,
            "INVALID_MOVE_DESTINATION",
            f"Cannot move {old_rel} into its own subtree: {new_rel}",
            path=new_rel,
        )

    if new_entry is not None:
        if body.no_clobber:
            return ApiResponse.success(
                data={
                    "old_path": old_rel,
                    "new_path": new_rel,
                    "old_version_path": old_full,
                    "new_version_path": new_full,
                    "scope": _scope_payload(scope),
                    "commit_id": "",
                    "skipped": True,
                    "reason": "destination exists",
                }
            )
        if body.no_target_directory and new_entry.type == "folder":
            raise _fs_error(400, "IS_DIRECTORY", f"Is a directory: {new_rel}", path=new_rel)
        if new_entry.type == "folder" and old_entry.type != "folder":
            raise _fs_error(400, "IS_DIRECTORY", f"Is a directory: {new_rel}", path=new_rel)
        if new_entry.type != "folder" and old_entry.type == "folder":
            raise _fs_error(400, "NOT_A_DIRECTORY", f"Not a directory: {new_rel}", path=new_rel)

    try:
        outcome = await commands.move(
            project_id,
            old_rel,
            new_rel,
            actor=_operator(auth),
            scope=scope["path"],
            message=body.message or f"ap move {old_rel} -> {new_rel}",
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return ApiResponse.success(
        data={
            "old_path": old_rel,
            "new_path": new_rel,
            "old_version_path": old_full,
            "new_version_path": new_full,
            "scope": _scope_payload(scope),
            "commit_id": result.commit_id,
            "skipped": False,
        }
    )


@router.post("/cp", response_model=ApiResponse)
async def copy(
    body: CopyRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="cp",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    old_rel = _clean_relative(body.old_path)
    new_rel = _clean_relative(body.new_path)
    if not old_rel:
        raise HTTPException(status_code=400, detail="Both old_path and new_path are required")
    _assert_not_excluded(old_rel, scope)
    _assert_not_excluded(new_rel, scope)
    # PUP-3: block writes landing inside a blocklisted path. The source
    # side is intentionally permissive so users can move/copy legacy
    # content OUT of a blocked directory while cleaning up.
    _assert_upload_policy(new_rel)

    old_full = _join_scope(scope["path"], old_rel)
    old_entry = _ops_stat(commands.ops, project_id, scope, old_rel)
    if old_entry is None:
        raise _fs_error(404, "NOT_FOUND", f"Path not found: {old_rel}", path=old_rel)
    if old_entry.type == "folder" and not body.recursive:
        raise _fs_error(400, "IS_DIRECTORY", f"Is a directory: {old_rel}", path=old_rel)

    new_rel, new_full, new_entry = _resolve_copy_move_destination(
        project_id,
        scope,
        commands.ops,
        old_rel,
        new_rel,
        target_directory=body.target_directory,
        no_target_directory=body.no_target_directory,
    )

    if new_entry is not None:
        if body.no_clobber:
            return ApiResponse.success(
                data={
                    "old_path": old_rel,
                    "new_path": new_rel,
                    "old_version_path": old_full,
                    "new_version_path": new_full,
                    "scope": _scope_payload(scope),
                    "commit_id": "",
                    "skipped": True,
                    "reason": "destination exists",
                }
            )
        if body.no_target_directory and new_entry.type == "folder":
            raise _fs_error(400, "IS_DIRECTORY", f"Is a directory: {new_rel}", path=new_rel)
        if new_entry.type == "folder" and old_entry.type != "folder":
            raise _fs_error(400, "IS_DIRECTORY", f"Is a directory: {new_rel}", path=new_rel)
        if new_entry.type != "folder" and old_entry.type == "folder":
            raise _fs_error(400, "NOT_A_DIRECTORY", f"Not a directory: {new_rel}", path=new_rel)

    try:
        outcome = await commands.copy(
            project_id,
            old_rel,
            new_rel,
            actor=_operator(auth),
            scope=scope["path"],
            message=body.message or f"ap copy {old_rel} -> {new_rel}",
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return ApiResponse.success(
        data={
            "old_path": old_rel,
            "new_path": new_rel,
            "old_version_path": old_full,
            "new_version_path": new_full,
            "scope": _scope_payload(scope),
            "commit_id": result.commit_id,
            "skipped": False,
        }
    )


@router.post("/rmdir", response_model=ApiResponse)
async def rmdir(
    body: RmdirRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="rmdir",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_paths = [_clean_relative(p) for p in (body.paths or [body.path])]
    rel_paths = [p for p in rel_paths if p]
    if not rel_paths:
        raise HTTPException(status_code=400, detail="Cannot remove the access point root")

    remove_paths: list[str] = []
    seen: set[str] = set()
    for rel_path in rel_paths:
        _assert_not_excluded(rel_path, scope)
        for candidate in _rmdir_chain(
            project_id,
            rel_path,
            scope,
            commands.ops,
            parents=body.parents,
        ):
            if candidate not in seen:
                remove_paths.append(candidate)
                seen.add(candidate)

    try:
        outcome = await commands.delete(
            project_id,
            remove_paths,
            actor=_operator(auth),
            scope=scope["path"],
            message=(
                f"ap rmdir {remove_paths[0]}"
                if len(remove_paths) == 1
                else f"ap rmdir {len(remove_paths)} directories"
            ),
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    data = {
        "paths": rel_paths,
        "removed_paths": remove_paths,
        "version_paths": [_join_scope(scope["path"], p) for p in remove_paths],
        "scope": _scope_payload(scope),
        "commit_id": result.commit_id,
        "removed": True,
    }
    if len(rel_paths) == 1:
        data["path"] = rel_paths[0]
        data["version_path"] = _join_scope(scope["path"], rel_paths[0])
    return ApiResponse.success(data=data)


@router.post("/rm", response_model=ApiResponse)
async def remove(
    body: RemoveRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="rm",
    )
    commands = VersionWriteCommandService(commands.ops.for_grant(auth["_runtime_grant"]))
    _ensure_writable(scope)
    rel_paths = [_clean_relative(p) for p in (body.paths or [body.path])]
    rel_paths = [p for p in rel_paths if p]
    if not rel_paths:
        raise HTTPException(status_code=400, detail="Cannot remove the access point root")

    existing_paths: list[str] = []
    missing_paths: list[str] = []
    for rel_path in rel_paths:
        _assert_not_excluded(rel_path, scope)
        entry = _ops_stat(commands.ops, project_id, scope, rel_path)
        if entry is None:
            missing_paths.append(rel_path)
            continue
        if entry.type == "folder" and not body.recursive:
            raise HTTPException(status_code=400, detail=f"Is a directory: {rel_path}")
        existing_paths.append(rel_path)

    if missing_paths and not body.force:
        raise HTTPException(status_code=404, detail=f"Path not found: {missing_paths[0]}")
    if not existing_paths:
        data = {
            "paths": rel_paths,
            "version_paths": [_join_scope(scope["path"], p) for p in rel_paths],
            "scope": _scope_payload(scope),
            "commit_id": "",
            "removed": False,
        }
        if len(rel_paths) == 1:
            data["path"] = rel_paths[0]
            data["version_path"] = _join_scope(scope["path"], rel_paths[0])
        return ApiResponse.success(data=data)

    try:
        outcome = await commands.delete(
            project_id,
            existing_paths,
            actor=_operator(auth),
            scope=scope["path"],
            message=(
                f"ap delete {existing_paths[0]}"
                if len(existing_paths) == 1
                else f"ap delete {len(existing_paths)} paths"
            ),
            base_commit_id=body.base_commit_id,
            defer_projection=True,
            source_channel="access_cli",
        )
        result = outcome.result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ConcurrentMutationError, NativeRevisionConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    data = {
        "paths": existing_paths,
        "version_paths": [_join_scope(scope["path"], p) for p in existing_paths],
        "scope": _scope_payload(scope),
        "commit_id": result.commit_id,
        "removed": True,
    }
    if len(existing_paths) == 1:
        data["path"] = existing_paths[0]
        data["version_path"] = _join_scope(scope["path"], existing_paths[0])
    return ApiResponse.success(data=data)
