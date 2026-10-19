"""CLI AP-FS read: existing protocol behavior over admitted repository operations."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from fastapi.responses import Response

from src.common_schemas import ApiResponse
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.version_engine.admission.validation import validate_limit
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter

from .context import (
    _assert_not_excluded,
    _basename,
    _clean_relative,
    _entry_to_scoped_response,
    _filter_directories,
    _filter_entries,
    _join_scope,
    _ops_list_dir,
    _ops_list_tree,
    _ops_read_file,
    _ops_read_file_range,
    _ops_stat,
    _query_bool,
    _query_int,
    _query_optional_int,
    _resolve_auth,
    _scope_payload,
)


router = APIRouter()


_RECURSIVE_DEFAULT_LIMIT = 5000


_RECURSIVE_MAX_LIMIT = 50000


def _attach_timestamps(
    project_id: str,
    entries: list,
    ops: ProductOperationAdapter,
    *,
    extra_paths: list[str] | None = None,
) -> None:
    paths = [entry.path for entry in entries]
    if extra_paths:
        paths.extend(extra_paths)
    timestamps = ops.get_path_timestamps(project_id, paths)
    for entry in entries:
        data = timestamps.get(entry.path.strip("/")) or {}
        entry.created_at = data.get("created_at") or None
        entry.modified_at = data.get("modified_at") or None


@router.get("/ls", response_model=ApiResponse)
async def list_dir(
    path: str = Query("", description="Path relative to the access point scope"),
    include_hidden: bool = Query(False, description="Include entries whose names begin with '.'"),
    include_size: bool = Query(False, description="Include file sizes by reading file blobs"),
    include_times: bool = Query(
        False, description="Include timestamps derived from version history"
    ),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="ls",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    _assert_not_excluded(rel_path, scope)

    full_path = _join_scope(scope["path"], rel_path)
    target = _ops_stat(ops, project_id, scope, rel_path, include_size=include_size)
    if target is None and rel_path:
        raise HTTPException(status_code=404, detail=f"Path not found: {rel_path}")
    target_type = target.type if target else ""
    if target and target.type != "folder":
        # POSIX ls on a file lists that file itself, not the parent directory.
        entries = [target]
    else:
        entries = _filter_entries(
            _ops_list_dir(ops, project_id, scope, rel_path, include_size=include_size),
            scope,
            include_hidden=include_hidden,
        )
    if include_times:
        _attach_timestamps(project_id, entries, ops, extra_paths=[full_path])
    return ApiResponse.success(
        data={
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "target_type": target_type,
            "entries": [_entry_to_scoped_response(e, scope) for e in entries],
            "head_commit_id": ops.get_head_commit_id(project_id),
        }
    )


@router.get("/tree", response_model=ApiResponse)
async def tree(
    path: str = Query("", description="Path relative to the access point scope"),
    max_depth: int = Query(-1, description="Maximum recursion depth, -1 = unlimited"),
    limit: int = Query(
        _RECURSIVE_DEFAULT_LIMIT,
        description="Maximum entries returned before truncation",
    ),
    include_hidden: bool = Query(False, description="Include entries whose names begin with '.'"),
    include_size: bool = Query(False, description="Include file sizes by reading file blobs"),
    include_times: bool = Query(
        False, description="Include timestamps derived from version history"
    ),
    directories_only: bool = Query(False, description="Only include directories"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="tree",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    _assert_not_excluded(rel_path, scope)
    max_depth = _query_int(max_depth, -1)
    include_hidden = _query_bool(include_hidden)
    include_size = _query_bool(include_size)
    include_times = _query_bool(include_times)
    directories_only = _query_bool(directories_only)
    safe_limit = validate_limit(
        _query_int(limit, _RECURSIVE_DEFAULT_LIMIT),
        default=_RECURSIVE_DEFAULT_LIMIT,
        maximum=_RECURSIVE_MAX_LIMIT,
    )

    full_path = _join_scope(scope["path"], rel_path)
    target = _ops_stat(ops, project_id, scope, rel_path, include_size=include_size)
    if target is None and rel_path:
        raise HTTPException(status_code=404, detail=f"Path not found: {rel_path}")
    target_type = target.type if target else ""
    if target and target.type != "folder":
        entries = [] if directories_only else [target]
        truncated = False
    else:
        entries = _filter_entries(
            _ops_list_tree(
                ops,
                project_id,
                scope,
                rel_path,
                max_depth=max_depth,
                include_size=include_size,
                max_entries=safe_limit + 1,
            ),
            scope,
            include_hidden=include_hidden,
        )
        truncated = len(entries) > safe_limit
        if truncated:
            entries = entries[:safe_limit]
        if directories_only:
            entries = _filter_directories(entries)
    if include_times:
        _attach_timestamps(project_id, entries, ops, extra_paths=[full_path])
    response_entries = [_entry_to_scoped_response(e, scope) for e in entries]
    return ApiResponse.success(
        data={
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "target_type": target_type,
            "directories_only": directories_only,
            "limit": safe_limit,
            "returned_count": len(response_entries),
            "complete": not truncated,
            "truncated": truncated,
            "truncation_reason": "entry_limit_exceeded" if truncated else "",
            "entries": response_entries,
            "head_commit_id": ops.get_head_commit_id(project_id),
        }
    )


@router.get("/cat", response_model=ApiResponse)
async def read_file(
    path: str = Query(..., description="File path relative to the access point scope"),
    structured: bool = Query(False, description="Parse JSON files into structured content"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="cat",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    if not rel_path:
        raise HTTPException(status_code=400, detail="File path is required")
    _assert_not_excluded(rel_path, scope)

    full_path = _join_scope(scope["path"], rel_path)
    try:
        content = _ops_read_file(ops, project_id, scope, rel_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")

    from src.version_engine.read.tree_reader import detect_type

    node_type = detect_type(full_path)
    content_json = None
    content_text = content.decode("utf-8", errors="replace")
    if structured and node_type == "json":
        try:
            content_json = _json.loads(content_text)
            content_text = None
        except ValueError:
            pass

    return ApiResponse.success(
        data={
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "type": node_type,
            "content": content_json,
            "content_text": content_text,
            "head_commit_id": ops.get_head_commit_id(project_id),
        }
    )


@router.get("/raw")
async def raw_file(
    path: str = Query(..., description="File path relative to the access point scope"),
    start: int = Query(0, ge=0, description="Start byte offset"),
    limit: int | None = Query(None, ge=0, description="Maximum bytes to return"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="download",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    if not rel_path:
        raise HTTPException(status_code=400, detail="File path is required")
    _assert_not_excluded(rel_path, scope)
    start = _query_int(start, 0)
    limit = _query_optional_int(limit)

    try:
        if hasattr(ops, "read_file_range_in_scope") or hasattr(ops, "read_file_range"):
            blob = _ops_read_file_range(
                ops,
                project_id,
                scope,
                rel_path,
                start=start,
                limit=limit,
            )
            chunk = blob.content
            total = blob.total_size
        else:
            content = _ops_read_file(ops, project_id, scope, rel_path)
            total = len(content)
            end = total if limit is None else min(total, start + limit)
            chunk = content[start:end]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")

    headers = {
        "Content-Length": str(len(chunk)),
        "Accept-Ranges": "bytes",
        "X-Puppyone-Path": rel_path,
        "X-Puppyone-Size": str(total),
    }
    if start or limit is not None:
        if chunk:
            range_end = start + len(chunk) - 1
            headers["Content-Range"] = f"bytes {start}-{range_end}/{total}"
        else:
            headers["Content-Range"] = f"bytes */{total}"

    return Response(
        content=chunk,
        media_type="application/octet-stream",
        headers=headers,
    )


@router.get("/stat", response_model=ApiResponse)
async def stat(
    path: str = Query("", description="Path relative to the access point scope"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="stat",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    _assert_not_excluded(rel_path, scope)

    full_path = _join_scope(scope["path"], rel_path)
    scope_head_commit_id = ops.get_scope_head_commit_id(project_id, scope["path"])
    if not rel_path:
        return ApiResponse.success(
            data={
                "path": "",
                "version_path": full_path,
                "scope": _scope_payload(scope),
                "exists": True,
                "type": "folder",
                "name": _basename(scope["path"]) if scope["path"] else "",
                "content_hash": "",
                "size_bytes": 0,
                "mime_type": "inode/directory",
                "children_count": None,
                "integrity_status": "ok",
                "head_commit_id": scope_head_commit_id,
                "scope_head_commit_id": scope_head_commit_id,
                "metadata_source": "scope_state",
                "timestamp_source": "scope_state",
                "compatibility": {
                    "mode": "pseudo",
                    "uid": "pseudo",
                    "gid": "pseudo",
                    "device": "not_modeled",
                    "inode": "not_modeled",
                    "links": "pseudo",
                },
            }
        )

    head_commit_id = ops.get_head_commit_id(project_id)
    entry = _ops_stat(ops, project_id, scope, rel_path, include_size=True)
    if not entry:
        return ApiResponse.success(
            data={
                "path": rel_path,
                "version_path": full_path,
                "scope": _scope_payload(scope),
                "exists": False,
                "type": "",
                "name": "",
                "head_commit_id": head_commit_id,
                "scope_head_commit_id": scope_head_commit_id,
            }
        )
    _attach_timestamps(project_id, [entry], ops, extra_paths=[full_path])
    data = _entry_to_scoped_response(entry, scope)
    data["exists"] = True
    data["scope"] = _scope_payload(scope)
    data["head_commit_id"] = head_commit_id
    data["scope_head_commit_id"] = scope_head_commit_id
    data["metadata_source"] = "version_tree"
    data["timestamp_source"] = "version_history"
    data["compatibility"] = {
        "mode": "pseudo",
        "uid": "pseudo",
        "gid": "pseudo",
        "device": "not_modeled",
        "inode": "not_modeled",
        "links": "pseudo",
    }
    return ApiResponse.success(data=data)
