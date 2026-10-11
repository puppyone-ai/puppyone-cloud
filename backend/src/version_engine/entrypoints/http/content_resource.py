"""Read-only website resource entry. Reuses the admitted native read context."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from src.common_schemas import ApiResponse
from src.infra.file_formats import is_text_format
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.version_engine.adapters.product.operation_adapter import ProductOperationAdapter
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.version_engine.domain.errors import (
    ObjectNotFoundError,
    PathNotFoundError,
    VersionReadError,
)
from src.version_engine.entrypoints.http.content_helpers import (
    display_git_path,
    ensure_project_access,
    entry_to_response,
)
from src.version_engine.entrypoints.http.content_read_context import (
    _product_read,
    _raise_storage_integrity_error,
    _raise_version_read_error,
    _read_request_path,
)
from src.version_engine.entrypoints.http.schemas import VersionEntryResponse

resource_router = APIRouter()
TEXT_PREVIEW_BYTES = 128 * 1024
IMAGE_PREVIEW_BYTES = 2 * 1024 * 1024
SAFE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}


class ResourceResponse(BaseModel):
    kind: Literal["file", "directory"]
    path: str
    entry: VersionEntryResponse
    repository_revision: dict[str, Any]
    entries: list[VersionEntryResponse] = Field(default_factory=list)
    next_cursor: str | None = None
    preview: Literal["text", "image", "unavailable"] = "unavailable"
    content_text: str | None = None
    content_base64: str | None = None
    truncated: bool = False


def _page_offset(cursor: str | None, binding: list) -> int:
    if not cursor:
        return 0
    try:
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(value, dict)
            or type(value.get("offset")) is not int
            or value["offset"] < 0
        ):
            raise ValueError("invalid offset")
        if value.get("binding") != binding:
            raise HTTPException(409, "Directory changed. Reload it before continuing.")
        return value["offset"]
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid directory cursor") from exc


def _page_cursor(offset: int | None, binding: list) -> str | None:
    if offset is None:
        return None
    return base64.urlsafe_b64encode(
        json.dumps({"binding": binding, "offset": offset}).encode()
    ).decode()


def _file_preview(
    read, project_id: str, path: str, result: ResourceResponse, *, full_text: bool = False
) -> None:
    entry = result.entry
    if entry.type == "gitlink":
        return
    mime = entry.mime_type or "application/octet-stream"
    image = mime in SAFE_IMAGE_TYPES and entry.git_mode != "120000"
    text = (
        is_text_format(path)
        or mime.startswith("text/")
        or mime
        in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/yaml",
            "application/x-yaml",
            "image/svg+xml",
        }
        or entry.type in {"json", "markdown"}
        or entry.git_mode == "120000"
    )
    if not image and not text:
        return
    if full_text and mime.startswith("image/") and entry.git_mode != "120000":
        # Full Workspace views use the native image loader. Do not read/encode a
        # thumbnail here only to fetch the same large blob again through /raw.
        return
    # Native reads verify the entire Git blob before slicing. The cap bounds
    # the response, not physical S3 transfer; no unverified partial bytes escape.
    limit = IMAGE_PREVIEW_BYTES if image else TEXT_PREVIEW_BYTES
    if full_text and text and not image:
        # Same complete text contract as /cat, in this already pinned read.
        # Binary media remain lazy and use the native authorized file viewers.
        content = read.read_file(project_id, path)
        entry.size_bytes = len(content)
    else:
        blob = read.read_file_range(project_id, path, limit=limit)
        content = blob.content
        entry.size_bytes = blob.total_size
        result.truncated = blob.total_size > limit
    if image:
        if not result.truncated:
            result.preview = "image"
            result.content_base64 = base64.b64encode(content).decode()
        return
    try:
        # Ignore only an incomplete final UTF-8 codepoint in a truncated preview.
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        text_body = decoder.decode(content, final=not result.truncated)
        if "\x00" not in text_body:
            result.preview = "text"
            result.content_text = text_body
    except UnicodeDecodeError:
        pass


@resource_router.get("/{project_id}/resource", response_model=ApiResponse[ResourceResponse])
def read_resource(
    project_id: str,
    response: Response,
    path: str = Query(""),
    path_bytes_b64: str | None = Query(None),
    cursor: str | None = Query(None, max_length=8192),
    limit: int = Query(100, ge=1, le=200),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
    text_mode: Literal["preview", "full"] = "preview",
):
    grant = ensure_project_access(authorization, current_user, project_id)
    # Query parsing has already decoded once. A literal "%2F" in a Git name
    # remains literal; never unquote again or normalize distinct repository paths.
    clean = _read_request_path(path, path_bytes_b64)
    if path_bytes_b64 is None and (
        clean != path or (path and any(part == "" for part in path.split("/")))
    ):
        raise HTTPException(400, "Expected a repository-relative path")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    try:
        with _product_read(ops, project_id, grant, byte_path=path_bytes_b64 is not None) as (
            read,
            revision,
        ):
            entry = read.stat(project_id, clean, include_children_count=False)
            if entry is None:
                raise HTTPException(404, "File or directory not found")
            directory = entry.type == "folder"
            result = ResourceResponse(
                kind="directory" if directory else "file",
                path=display_git_path(clean),
                entry=entry_to_response(entry),
                repository_revision=revision,
            )
            if directory:
                binding = [project_id, clean, revision["generation"], revision["tree_oid"]]
                entries, offset = read.list_dir_page(
                    project_id, clean, offset=_page_offset(cursor, binding), limit=limit
                )
                result.entries = [entry_to_response(item) for item in entries]
                result.next_cursor = _page_cursor(offset, binding)
            else:
                if cursor:
                    raise HTTPException(400, "A file does not accept a directory cursor")
                _file_preview(read, project_id, clean, result, full_text=text_mode == "full")
            return ApiResponse.success(data=result)
    except ObjectNotFoundError as exc:
        _raise_storage_integrity_error(project_id, clean, exc)
    except (FileNotFoundError, PathNotFoundError) as exc:
        raise HTTPException(404, "File or directory not found") from exc
    except VersionReadError as exc:
        _raise_version_read_error(clean, exc)
