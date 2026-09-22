"""Local shadow snapshot ingest API (I2).

See ``docs/architecture/08-shadow-snapshots.md`` for the conceptual
model. This module implements the server-side surface a local
PuppyOne client daemon (or any equivalent integration) uses to publish
its working-tree manifest:

  POST   /api/v1/local-snapshots                upsert a snapshot for the caller
  GET    /api/v1/local-snapshots                list the caller's snapshots
  GET    /api/v1/local-snapshots/{snapshot_id}  one snapshot (manifest pulled from S3)
  DELETE /api/v1/local-snapshots/{snapshot_id}  drop one snapshot
  POST   /api/v1/local-snapshots/{snapshot_id}/blobs    upload referenced blobs (I3)
  POST   /api/v1/local-snapshots/{snapshot_id}/promote  promote to a real commit (I5)

The endpoint refuses spoofing: ``user_id`` is always taken from the
authenticated JWT, never from the request body.

Storage layout:

  Supabase ``local_shadow_snapshots`` row    — lightweight identity +
                                                statistics (id, project_id,
                                                user_id, machine_id,
                                                ref_name, tree_hash,
                                                file_count, total_bytes,
                                                timestamps). NO manifest
                                                JSON, no previews, no
                                                blob_hashes — those moved
                                                to S3.

  S3 ``shadow-snapshots/{project_id}/{snapshot_id}/manifest.json``
                                              — one document with
                                                ``{manifest[], previews{},
                                                blob_hashes[]}``. Single
                                                PUT covers all three; one
                                                GET retrieves them.

Why the move: the JSONB columns were capped at 8 MiB after encoding
because larger payloads pressed against Postgres TOAST + JSONB
performance. The cap broke large-monorepo onboarding (100k+ files
with previews easily blow past 8 MiB). S3 has effectively no size
ceiling for our purposes; we keep only the per-file and entry-count
sanity checks.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from src.common_schemas import ApiResponse
from src.infra.s3.exceptions import S3FileNotFoundError
from src.infra.s3.service import get_s3_service_instance
from src.infra.supabase.client import SupabaseClient
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.utils.logger import log_info, log_warning
from src.version_engine.entrypoints.http.content_helpers import (
    ensure_project_access,
    ensure_write_access,
)


router = APIRouter()


# ── Sanity caps (see 08-shadow-snapshots.md §3) ──
#
# The JSONB total-size cap is gone — manifests live in S3 now. We
# keep the entry-count and per-file caps because they bound what
# the server has to JSON-encode in one request and what an individual
# blob upload is later going to be allowed to push.
#
# ``_MAX_BYTES_PER_FILE`` must stay in lockstep with
# ``upload_policy.PER_FILE_MAX_BYTES`` (PUP-3 cross-language
# constant). If a file is upload-able through ``/ap-fs/upload`` or
# ``/git/push``, the desktop client's shadow-snapshot path MUST be
# able to register it on the manifest too — otherwise the same file
# accepted by web/CLI gets rejected by the local mirror and the
# two paths can't interoperate.
_MAX_FILES_PER_SNAPSHOT = 100_000
_MAX_BYTES_PER_FILE = 100 * 1024 * 1024
_VALID_FILE_MODES = frozenset({"100644", "100755", "120000", "40000"})

_MANIFEST_S3_CONTENT_TYPE = "application/json"


# ── Schemas ────────────────────────────────────────────────────


class ShadowSnapshotEntry(BaseModel):
    path: str
    mode: str = "100644"
    blob_hash: str
    size: int = 0
    mtime: str | None = None
    ignored: bool = False
    preview: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ShadowSnapshotEntry":
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(f"invalid path: {self.path!r}")
        if self.mode not in _VALID_FILE_MODES:
            raise ValueError(f"unsupported file mode: {self.mode}")
        if self.size < 0 or self.size > _MAX_BYTES_PER_FILE:
            raise ValueError(
                f"size out of range for {self.path}: {self.size} bytes "
                f"(limit {_MAX_BYTES_PER_FILE})"
            )
        if len(self.blob_hash) != 40 or any(
            c not in "0123456789abcdef" for c in self.blob_hash
        ):
            raise ValueError(f"blob_hash must be 40-hex SHA-1: {self.blob_hash!r}")
        return self


class UpsertShadowSnapshotRequest(BaseModel):
    project_id: str
    machine_id: str = ""
    ref_name: str = "main"
    tree_hash: str = ""
    manifest: list[ShadowSnapshotEntry]
    previews: dict[str, str] = Field(default_factory=dict)
    # Explicit opt-in (08-shadow-snapshots.md §1): shadow content is
    # user-private by default. Only when the owner sets this true may the
    # snapshot be read through the cross-user ``--ref local:`` grep path.
    grep_shared: bool = False


def _enforce_entry_count(req: UpsertShadowSnapshotRequest) -> None:
    """Reject manifests with too many entries.

    The 100k cap is well above any normal project (Linux kernel is
    ~80k tracked files) and below the size where PostgREST request
    parsing or our JSON-encoding step becomes painful.
    """
    if len(req.manifest) > _MAX_FILES_PER_SNAPSHOT:
        raise HTTPException(
            status_code=413,
            detail={
                "limit": "manifest entry count",
                "actual": len(req.manifest),
                "cap": _MAX_FILES_PER_SNAPSHOT,
                "message": (
                    f"shadow snapshot has {len(req.manifest)} entries; "
                    f"limit is {_MAX_FILES_PER_SNAPSHOT}"
                ),
            },
        )


class ShadowSnapshotResponse(BaseModel):
    snapshot_id: str
    project_id: str
    user_id: str
    machine_id: str
    ref_name: str
    file_count: int
    total_bytes: int
    tree_hash: str
    updated_at: str


class UpsertShadowSnapshotResponse(ShadowSnapshotResponse):
    blob_hashes_present_on_server: list[str] = Field(default_factory=list)
    blob_hashes_missing_on_server: list[str] = Field(default_factory=list)


# ── S3 helpers ─────────────────────────────────────────────────


def _manifest_s3_key(project_id: str, snapshot_id: str) -> str:
    """Path inside the shared S3 bucket where the snapshot's manifest
    document lives. Stable for the snapshot's lifetime — overwriting
    the same key on every upsert is the intended pattern."""
    return f"shadow-snapshots/{project_id}/{snapshot_id}/manifest.json"


async def _put_manifest_to_s3(
    *,
    project_id: str,
    snapshot_id: str,
    manifest: list[dict],
    previews: dict[str, str],
    blob_hashes: list[str],
) -> None:
    """Serialise the three logical chunks as one JSON document and PUT
    it. Single object + single PUT keeps the upsert atomic on the S3
    side (no partial states with manifest present but previews
    missing, etc.)."""
    document = {
        "version": 1,
        "snapshot_id": snapshot_id,
        "manifest": manifest,
        "previews": previews,
        "blob_hashes": blob_hashes,
    }
    body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    s3 = get_s3_service_instance()
    await s3.upload_file(
        key=_manifest_s3_key(project_id, snapshot_id),
        content=body,
        content_type=_MANIFEST_S3_CONTENT_TYPE,
    )


async def _get_manifest_from_s3(
    project_id: str, snapshot_id: str,
) -> dict[str, Any] | None:
    """Inverse of ``_put_manifest_to_s3``. Returns the parsed JSON
    dict or ``None`` if the manifest is missing.

    ``None`` is a legitimate state — a row created without a manifest
    upload, or one whose S3 object got deleted out-of-band. Callers
    decide how to surface that (404, empty array, etc.)."""
    s3 = get_s3_service_instance()
    try:
        raw = await s3.download_file(_manifest_s3_key(project_id, snapshot_id))
    except S3FileNotFoundError:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log_warning(
            f"[shadow-snapshot] manifest at "
            f"{_manifest_s3_key(project_id, snapshot_id)} is unreadable: {exc}"
        )
        return None


async def _delete_manifest_from_s3(project_id: str, snapshot_id: str) -> None:
    """Best-effort: a missing object is fine, any other failure is
    logged but doesn't fail the DELETE endpoint (the DB row is the
    authoritative existence signal; an orphan S3 object is cheap to
    GC later)."""
    s3 = get_s3_service_instance()
    try:
        await s3.delete_file(_manifest_s3_key(project_id, snapshot_id))
    except Exception as exc:  # noqa: BLE001
        log_warning(
            f"[shadow-snapshot] delete manifest failed for "
            f"{project_id}/{snapshot_id}: {exc}"
        )


# ── TTL reaper (GAP-10) ─────────────────────────────────────────


async def reap_stale_shadow_snapshots(
    *,
    ttl_seconds: int,
    max_per_run: int = 500,
) -> dict:
    """Delete shadow snapshots not refreshed within ``ttl_seconds``.

    Shadow snapshots are an ephemeral projection of a teammate's
    un-pushed working tree (``08-shadow-snapshots.md``). Without a reaper
    the ``local_shadow_snapshots`` rows and their S3 ``manifest.json``
    objects accumulate forever (GAP-10). The referenced blobs live in the
    shared content-addressed object store and are reclaimed separately by
    the object GC, so this only removes the row + manifest — mirroring the
    user-facing DELETE.

    Manifest is deleted before the DB row so a crash mid-run is
    self-healing: the surviving row is re-selected next run and its
    already-gone manifest delete is a harmless no-op.

    Returns ``{"scanned": N, "deleted": M}``.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(0, ttl_seconds))
    ).isoformat()
    client = SupabaseClient().client
    resp = (
        client.table("local_shadow_snapshots")
        .select("id, project_id")
        .lt("updated_at", cutoff)
        .limit(max(1, int(max_per_run)))
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return {"scanned": 0, "deleted": 0}

    ids: list[str] = []
    for row in rows:
        snapshot_id = row.get("id")
        project_id = row.get("project_id")
        if not snapshot_id or not project_id:
            continue
        await _delete_manifest_from_s3(project_id, snapshot_id)
        ids.append(snapshot_id)

    deleted = 0
    if ids:
        try:
            del_resp = (
                client.table("local_shadow_snapshots")
                .delete()
                .in_("id", ids)
                .execute()
            )
            deleted = len(getattr(del_resp, "data", None) or ids)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"[shadow-snapshot] reaper bulk delete failed: {exc}")

    if deleted:
        log_info(
            f"[shadow-snapshot] reaped {deleted} snapshot(s) older than "
            f"{ttl_seconds}s (scanned {len(rows)})"
        )
    return {"scanned": len(rows), "deleted": deleted}


# ── Endpoints ──────────────────────────────────────────────────


@router.post(
    "/local-snapshots",
    response_model=ApiResponse[UpsertShadowSnapshotResponse],
    summary="Upsert a shadow snapshot for the calling user",
)
async def upsert_snapshot(
    body: UpsertShadowSnapshotRequest,
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create or update a shadow snapshot. Identified by
    ``(project_id, user_id, machine_id, ref_name)`` — the same client
    can update its row over and over without rotating IDs.

    Flow:
      1. Resolve an existing ``snapshot_id`` via the natural key (or
         mint a fresh UUID4 for first upload). Stable id = stable S3
         key for the manifest.
      2. PUT the manifest document to S3 (overwrite in place).
      3. UPSERT the lightweight row in Supabase.

    On S3 failure we surface a 502 with no DB write — the caller can
    safely retry. On DB failure post-S3 we leave the S3 object as an
    orphan; the next successful upsert overwrites it and a periodic
    janitor (out of scope here) can sweep dead keys.
    """

    ensure_project_access(authorization, current_user, body.project_id)
    _enforce_entry_count(body)

    file_count = len(body.manifest)
    total_bytes = sum(e.size for e in body.manifest)
    blob_hashes = sorted({e.blob_hash for e in body.manifest})

    client = SupabaseClient().client
    natural_key_match = (
        client.table("local_shadow_snapshots")
        .select("id")
        .eq("project_id", body.project_id)
        .eq("user_id", current_user.user_id)
        .eq("machine_id", body.machine_id or "")
        .eq("ref_name", body.ref_name or "main")
        .maybe_single()
        .execute()
    )
    existing = getattr(natural_key_match, "data", None)
    snapshot_id = (existing or {}).get("id") or str(uuid.uuid4())

    # S3-first: if this fails we have nothing in DB pointing at it,
    # so the caller's retry is safe.
    await _put_manifest_to_s3(
        project_id=body.project_id,
        snapshot_id=snapshot_id,
        manifest=[e.model_dump() for e in body.manifest],
        previews=body.previews or {},
        blob_hashes=blob_hashes,
    )

    payload = {
        "id": snapshot_id,
        "project_id": body.project_id,
        "user_id": current_user.user_id,
        "machine_id": body.machine_id or "",
        "ref_name": body.ref_name or "main",
        "tree_hash": body.tree_hash or "",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "grep_shared": bool(body.grep_shared),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = (
        client.table("local_shadow_snapshots")
        .upsert(payload, on_conflict="project_id,user_id,machine_id,ref_name")
        .execute()
    )
    row = (resp.data or [{}])[0]

    # Best-effort: tell the client which blob hashes the server already
    # has so the I3 upload step knows what's missing.
    present, missing = _split_blobs_by_presence(body.project_id, blob_hashes)

    return ApiResponse.success(data=UpsertShadowSnapshotResponse(
        snapshot_id=row.get("id", snapshot_id),
        project_id=body.project_id,
        user_id=current_user.user_id,
        machine_id=body.machine_id or "",
        ref_name=body.ref_name or "main",
        file_count=file_count,
        total_bytes=total_bytes,
        tree_hash=body.tree_hash or "",
        updated_at=row.get("updated_at", payload["updated_at"]),
        blob_hashes_present_on_server=present,
        blob_hashes_missing_on_server=missing,
    ))


@router.get(
    "/local-snapshots",
    response_model=ApiResponse[list[ShadowSnapshotResponse]],
    summary="List the calling user's shadow snapshots",
)
async def list_snapshots(
    project_id: str = Query("", description="Filter by project (optional)"),
    machine_id: str = Query("", description="Filter by machine (optional)"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List metadata only — no manifest content. Cheap; touches DB
    only. Use ``GET /local-snapshots/{id}`` for the manifest body."""
    client = SupabaseClient().client
    builder = (
        client.table("local_shadow_snapshots")
        .select(
            "id, project_id, user_id, machine_id, ref_name, "
            "file_count, total_bytes, tree_hash, updated_at",
        )
        .eq("user_id", current_user.user_id)
    )
    if project_id:
        builder = builder.eq("project_id", project_id)
    if machine_id:
        builder = builder.eq("machine_id", machine_id)
    rows = (builder.order("updated_at", desc=True).limit(200).execute()).data or []
    return ApiResponse.success(data=[
        ShadowSnapshotResponse(
            snapshot_id=row["id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            machine_id=row.get("machine_id", "") or "",
            ref_name=row.get("ref_name", "") or "",
            file_count=row.get("file_count", 0) or 0,
            total_bytes=row.get("total_bytes", 0) or 0,
            tree_hash=row.get("tree_hash", "") or "",
            updated_at=row.get("updated_at", "") or "",
        )
        for row in rows
    ])


@router.get(
    "/local-snapshots/{snapshot_id}",
    summary="Read one shadow snapshot (manifest pulled from S3)",
)
async def get_snapshot(
    snapshot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Returns the full snapshot document: row metadata merged with
    the manifest JSON downloaded from S3. Missing manifest object is
    returned with empty ``manifest`` / ``previews`` / ``blob_hashes``
    rather than 500'ing — the DB row is the authoritative existence
    signal."""
    client = SupabaseClient().client
    resp = (
        client.table("local_shadow_snapshots")
        .select("*")
        .eq("id", snapshot_id)
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="snapshot not found")

    manifest_doc = await _get_manifest_from_s3(row["project_id"], snapshot_id)
    merged = dict(row)
    if manifest_doc is None:
        merged["manifest"] = []
        merged["previews"] = {}
        merged["blob_hashes"] = []
        merged["manifest_status"] = "missing"
    else:
        merged["manifest"] = manifest_doc.get("manifest") or []
        merged["previews"] = manifest_doc.get("previews") or {}
        merged["blob_hashes"] = manifest_doc.get("blob_hashes") or []
        merged["manifest_status"] = "ok"
    return ApiResponse.success(data=merged)


@router.delete(
    "/local-snapshots/{snapshot_id}",
    summary="Delete a shadow snapshot",
)
async def delete_snapshot(
    snapshot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Removes the DB row and the S3 manifest. The S3 delete is
    best-effort: the DB row going away is what makes the snapshot
    'gone' from the user's perspective."""
    client = SupabaseClient().client
    # Read the project_id BEFORE deleting so we know which S3 key to
    # remove. Selecting + deleting is two round-trips, but a snapshot
    # delete is rare and we'd rather over-pay than orphan-leak.
    resp = (
        client.table("local_shadow_snapshots")
        .select("project_id")
        .eq("id", snapshot_id)
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    row = getattr(resp, "data", None)
    if not row:
        raise HTTPException(status_code=404, detail="snapshot not found")
    project_id = row["project_id"]

    deleted_resp = (
        client.table("local_shadow_snapshots")
        .delete()
        .eq("id", snapshot_id)
        .eq("user_id", current_user.user_id)
        .execute()
    )
    deleted = len(deleted_resp.data or [])
    if deleted == 0:
        raise HTTPException(status_code=404, detail="snapshot not found")

    await _delete_manifest_from_s3(project_id, snapshot_id)
    return ApiResponse.success(data={"deleted": True})


# ── Eager blob upload (I3) ───────────────────────────────────────


class _BlobUploadEntry(BaseModel):
    """One blob the client wants the server to absorb.

    ``content`` is base64-encoded bytes; the API accepts JSON so we
    can't ship raw octets. ``blob_hash`` MUST be the SHA-1 the client
    claims — the server verifies by re-hashing the decoded bytes and
    rejects with HTTP 400 on mismatch (preventing hash-poisoning where
    a bad client tells us "this is blob X" but ships Y's bytes).
    """
    blob_hash: str
    content: str

    @model_validator(mode="after")
    def _validate(self) -> "_BlobUploadEntry":
        if len(self.blob_hash) != 40 or any(
            c not in "0123456789abcdef" for c in self.blob_hash
        ):
            raise ValueError(f"blob_hash must be 40-hex SHA-1: {self.blob_hash!r}")
        return self


class _BlobUploadRequest(BaseModel):
    blobs: list[_BlobUploadEntry]


class _BlobUploadResponse(BaseModel):
    snapshot_id: str
    accepted_count: int
    rejected_hashes: list[str] = Field(default_factory=list)
    server_present_count: int


@router.post(
    "/local-snapshots/{snapshot_id}/blobs",
    response_model=ApiResponse[_BlobUploadResponse],
    summary="Eagerly upload manifest-referenced blobs (I3)",
)
async def upload_snapshot_blobs(
    snapshot_id: str,
    body: _BlobUploadRequest,
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stream a batch of blob bodies into the project object store.

    Per ``08-shadow-snapshots.md §4`` (Object availability): the
    manifest can reference blob hashes the server has never seen;
    promotion to a real commit is blocked until those bytes land.
    This endpoint is the I3 piece — push the missing blobs ahead of
    time, then call ``/promote`` (I5) to land them as a Git commit
    through the engine.

    Auth: the snapshot's owner only (by user_id).
    """
    import base64
    import hashlib

    client = SupabaseClient().client
    snap = (
        client.table("local_shadow_snapshots")
        .select("id, project_id, user_id")
        .eq("id", snapshot_id)
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    snap_row = getattr(snap, "data", None)
    if not snap_row:
        raise HTTPException(status_code=404, detail="snapshot not found")

    project_id = snap_row["project_id"]
    # Re-validate project write-access at push time. The snapshot row's
    # user_id match is NOT sufficient: a user's access may have been revoked
    # after the snapshot was created, and blob upload mutates the project
    # object store. Mirror the write-role gate the snapshot-create path uses.
    ensure_write_access(authorization, current_user, project_id)

    from src.version_engine.bootstrap.dependencies import (
        build_worker_version_engine_container,
    )
    repo = build_worker_version_engine_container().repo_manager.get_server_repo(project_id)
    store = repo.store

    accepted = 0
    rejected: list[str] = []
    for entry in body.blobs:
        try:
            data = base64.b64decode(entry.content, validate=True)
        except Exception:
            rejected.append(entry.blob_hash)
            continue
        # Hash-verify so a malicious client can't poison blob_hash → bytes.
        computed = hashlib.sha1(  # noqa: S324 — Git uses SHA-1 by convention
            f"blob {len(data)}\0".encode() + data,
        ).hexdigest()
        if computed != entry.blob_hash:
            rejected.append(entry.blob_hash)
            continue
        try:
            # ``put_blob`` frames + zlib-wraps + writes through the
            # canonical Git ObjectStore; returns the hash it stored at.
            # asyncio.to_thread because put_blob ultimately goes through
            # the sync S3 bridge.
            written_hash = await asyncio.to_thread(store.put_blob, data)
            if written_hash != entry.blob_hash:
                log_warning(
                    f"[shadow-blob] hash divergence: client={entry.blob_hash[:8]} "
                    f"server={written_hash[:8]} project={project_id}",
                )
                rejected.append(entry.blob_hash)
                continue
            accepted += 1
        except Exception as exc:
            log_warning(
                f"[shadow-blob] put failed for {entry.blob_hash[:8]} on "
                f"project={project_id}: {exc}",
            )
            rejected.append(entry.blob_hash)

    # Re-check the full manifest's blob presence so the client sees an
    # updated missing list. The manifest now lives in S3 — pull it
    # down once and pluck ``blob_hashes`` from there.
    manifest_doc = await _get_manifest_from_s3(project_id, snapshot_id)
    manifest_blobs = (manifest_doc or {}).get("blob_hashes") or []
    present, _missing = _split_blobs_by_presence(project_id, manifest_blobs)

    return ApiResponse.success(data=_BlobUploadResponse(
        snapshot_id=snapshot_id,
        accepted_count=accepted,
        rejected_hashes=rejected,
        server_present_count=len(present),
    ))


# ── Promote (I5) ─────────────────────────────────────────────────


class _PromoteRequest(BaseModel):
    scope_path: str = ""
    message: str = ""


class _PromoteResponse(BaseModel):
    snapshot_id: str
    commit_id: str
    project_id: str
    scope_path: str


@router.post(
    "/local-snapshots/{snapshot_id}/promote",
    response_model=ApiResponse[_PromoteResponse],
    summary="Promote a shadow snapshot to a real commit (I5)",
)
async def promote_snapshot(
    snapshot_id: str,
    body: _PromoteRequest,
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Turn a shadow manifest into a published Git commit.

    Per ``08-shadow-snapshots.md §6``: this is the last leg of the
    local-↔-cloud bridge. The manifest's blob hashes must all already
    be in the project's object store (call I3 first if anything is
    missing); promotion fails otherwise rather than landing a commit
    that references unreachable blobs.

    Routes through ``VersionWriteEngine.submit_version`` using a
    ``VersionSubmissionIntent`` — i.e. the same path a real Git push
    uses — so conflict policy, scope validation, audit, and outbox
    dispatch all fire normally. The snapshot row + S3 manifest are
    deleted after a successful promote to signal the bridge closed.
    """
    from src.version_engine.adapters.git.submission import submit_git_tree
    from src.version_engine.bootstrap.dependencies import (
        build_worker_version_engine_container,
    )
    from src.version_engine.write_engine.tree_objects import build_tree_from_files

    client = SupabaseClient().client
    snap = (
        client.table("local_shadow_snapshots")
        .select("*")
        .eq("id", snapshot_id)
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    snap_row = getattr(snap, "data", None)
    if not snap_row:
        raise HTTPException(status_code=404, detail="snapshot not found")

    project_id = snap_row["project_id"]
    # Re-validate project write-access before landing a commit — matching the
    # snapshot row's user_id is not enough (access may have been revoked).
    ensure_write_access(authorization, current_user, project_id)
    manifest_doc = await _get_manifest_from_s3(project_id, snapshot_id)
    manifest = (manifest_doc or {}).get("manifest") or []
    if not manifest:
        raise HTTPException(
            status_code=400,
            detail="snapshot manifest is empty or missing; nothing to promote",
        )

    blob_hashes = sorted({e.get("blob_hash") for e in manifest if e.get("blob_hash")})
    present, missing = _split_blobs_by_presence(project_id, blob_hashes)
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "blobs_missing",
                "message": (
                    f"{len(missing)} blob(s) referenced by the snapshot are not "
                    f"in the project object store. Upload them via "
                    f"POST /api/v1/local-snapshots/{{snapshot_id}}/blobs first."
                ),
                "missing_hashes_sample": missing[:10],
                "missing_count": len(missing),
                "present_count": len(present),
            },
        )

    container = build_worker_version_engine_container()
    repo = container.repo_manager.get_server_repo(project_id)

    files: dict[str, bytes] = {}
    for entry in manifest:
        path = entry.get("path", "")
        blob_hash = entry.get("blob_hash", "")
        if not path or not blob_hash:
            continue
        try:
            files[path] = repo.store.get(blob_hash)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"failed to read blob {blob_hash[:12]} for path "
                    f"{path!r}: {exc}"
                ),
            ) from exc

    tree_id = build_tree_from_files(repo.store, files)
    scope_path = (body.scope_path or "").strip("/")
    actor = f"user:{current_user.user_id}"
    base_commit_id = repo.get_scope_head_commit_id(scope_path) or ""

    from src.version_engine.write_engine.git_commit import build_git_commit
    promoted_message = body.message or (
        f"shadow snapshot promote ({snap_row.get('machine_id') or 'local'} "
        f"@ {snap_row.get('ref_name') or 'main'})"
    )
    client_commit = build_git_commit(
        repo,
        tree_sha=tree_id,
        parent_sha=base_commit_id,
        who=actor,
        message=promoted_message,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
    )

    submission_result = await submit_git_tree(
        container.repo_manager,
        project_id=project_id,
        scope_path=scope_path,
        actor=actor,
        base_commit_id=base_commit_id,
        proposed_tree_id=tree_id,
        client_commit_id=client_commit,
        proposed_files=files,
        message=promoted_message,
    )

    if submission_result.status not in {"ok", "merged"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "promotion_blocked",
                "status": submission_result.status,
                "pending_conflict_id": submission_result.pending_conflict_id,
                "message": (
                    "The snapshot tree could not be cleanly merged. "
                    "See the pending conflict and resolve, then retry promote."
                ),
            },
        )

    # Promotion succeeded — clean up DB row + S3 manifest. Both are
    # best-effort: a stale row or orphan S3 object can be cleaned up
    # manually, but a half-deleted snapshot shouldn't block the user.
    try:
        client.table("local_shadow_snapshots").delete().eq("id", snapshot_id).execute()
    except Exception as exc:
        log_warning(
            f"[shadow-promote] snapshot {snapshot_id} promoted to "
            f"commit {submission_result.commit_id} but DB cleanup "
            f"failed: {exc}",
        )
    await _delete_manifest_from_s3(project_id, snapshot_id)

    return ApiResponse.success(data=_PromoteResponse(
        snapshot_id=snapshot_id,
        commit_id=submission_result.commit_id,
        project_id=project_id,
        scope_path=scope_path,
    ))


# ── Helpers ────────────────────────────────────────────────────


def _split_blobs_by_presence(project_id: str, blob_hashes: list[str]) -> tuple[list[str], list[str]]:
    """Best-effort: ask the project's object store which blobs it has.

    We don't fail the upsert if the lookup blows up — it's metadata.
    """

    if not blob_hashes:
        return [], []
    try:
        from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container
        repo = build_worker_version_engine_container().repo_manager.get_server_repo(project_id)
        store = repo.store
        present = [h for h in blob_hashes if store.exists(h)]
        present_set = set(present)
        missing = [h for h in blob_hashes if h not in present_set]
        return present, missing
    except Exception:
        return [], list(blob_hashes)
