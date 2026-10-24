"""CLI AP-FS router: existing protocol behavior over admitted repository operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header

from src.common_schemas import ApiResponse
from src.file_operations.upload_policy import (
    DEFAULT_BLOCKLIST_SEGMENTS as POLICY_BLOCKLIST_SEGMENTS,
    PER_BATCH_MAX_BYTES as POLICY_PER_BATCH_MAX_BYTES,
    PER_BATCH_MAX_FILES as POLICY_PER_BATCH_MAX_FILES,
    PER_FILE_MAX_BYTES as POLICY_PER_FILE_MAX_BYTES,
    PREFLIGHT_BYTES_THRESHOLD as POLICY_PREFLIGHT_BYTES_THRESHOLD,
    PREFLIGHT_FILE_THRESHOLD as POLICY_PREFLIGHT_FILE_THRESHOLD,
)
from src.version_engine.admission.permission import is_mode_writable
from src.platform.access.filesystem.capabilities import scoped_fs_capabilities, upload_policy_document

from . import read, search, write
from .context import _resolve_auth, _scope_payload


router = APIRouter(prefix="/ap-fs", tags=["access-point-fs"])


@router.get("/semantics", response_model=ApiResponse)
async def semantics(
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="semantics",
    )
    data = scoped_fs_capabilities(
        writable=is_mode_writable(str(scope.get("mode", "r"))),
        upload_policy=upload_policy_document(
            per_file_max_bytes=POLICY_PER_FILE_MAX_BYTES,
            per_batch_max_files=POLICY_PER_BATCH_MAX_FILES,
            per_batch_max_bytes=POLICY_PER_BATCH_MAX_BYTES,
            preflight_file_threshold=POLICY_PREFLIGHT_FILE_THRESHOLD,
            preflight_bytes_threshold=POLICY_PREFLIGHT_BYTES_THRESHOLD,
            blocklist_segments=POLICY_BLOCKLIST_SEGMENTS,
        ),
    )
    data["project_id"] = project_id
    data["scope"] = _scope_payload(scope)
    return ApiResponse.success(data=data)


@router.post("/admin/object-integrity", response_model=ApiResponse)
def retired_object_repair():
    raise HTTPException(status_code=410, detail="Physical object repair is an operator task")


@router.post("/admin/fs-index/rebuild", response_model=ApiResponse)
@router.post("/admin/text-index/rebuild", response_model=ApiResponse)
def rebuild_native_index():
    raise HTTPException(
        status_code=410, detail="Native indexes rebuild automatically from ref changes"
    )



router.include_router(read.router)
router.include_router(write.router)
router.include_router(search.router)
