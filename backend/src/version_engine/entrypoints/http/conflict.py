"""Conflict resolution API.

V1 endpoints to close the loop on ``manual_review`` and (later)
``agent_review`` pending transactions:

  GET  /api/v1/content/{project_id}/conflicts/pending
       List pending conflict rows with ``status='pending'`` for this
       project. The frontend resolver UI uses this to render the
       reviewer's queue.

  GET  /api/v1/content/{project_id}/conflicts/{pending_conflict_id}
       Full conflict detail (base / current / proposed trees, the
       structured conflict_records list, the FK ``transaction_id``).

  POST /api/v1/content/{project_id}/conflicts/{pending_conflict_id}/resolve
       Apply a ``ConflictResolutionIntent``. Body chooses ``accept``
       (with ``resolution_tree_id`` or ``resolution_files``) or
       ``reject``. Returns the committed commit_id on success, or a
       follow-up pending_conflict_id if the resolution itself raced
       with another write and re-entered ``pending``.

The router mounts under the existing content API tree
(``/api/v1/content/{project_id}/...``) so the auth pattern matches
``content_history``: write access required to resolve, project access
required to list.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from src.common_schemas import ApiResponse
from src.infra.supabase.client import SupabaseClient
from src.version_engine.write_engine.engine import VersionWriteEngine
from src.version_engine.bootstrap.dependencies import get_version_write_engine
from src.version_engine.domain.intents import ConflictResolutionIntent
from src.version_engine.infrastructure.supabase.db_names import CONFLICTS_TABLE
from src.version_engine.entrypoints.http.content_helpers import (
    ensure_project_access,
    ensure_write_access,
)
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService


router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────


class PendingConflictSummary(BaseModel):
    pending_conflict_id: str
    transaction_id: int | None = None
    scope_path: str
    base_commit_id: str
    current_commit_id: str
    client_commit_id: str
    proposed_tree_id: str
    policy: str
    status: str
    resolver_actor: str
    resolver_kind: str
    created_at: str
    changed_paths: list[str] = Field(default_factory=list)


class PendingConflictDetail(PendingConflictSummary):
    conflict_records: list[dict] = Field(default_factory=list)
    resolution_detail: dict = Field(default_factory=dict)


class ResolveConflictRequest(BaseModel):
    decision: str = Field(..., pattern=r"^(accept|reject)$")
    resolution_tree_id: str = ""
    resolution_files: dict[str, str] | None = None  # base64-encoded bytes
    resolution_message: str = ""

    @model_validator(mode="after")
    def _accept_needs_tree_or_files(self) -> "ResolveConflictRequest":
        if self.decision == "accept" and not self.resolution_tree_id and not self.resolution_files:
            raise ValueError("accept requires resolution_tree_id or resolution_files")
        return self


class ResolveConflictResponse(BaseModel):
    status: str
    commit_id: str = ""
    pending_conflict_id: str = ""
    follow_up_pending_conflict_id: str = ""
    reason: str = ""


# ── Endpoints ──────────────────────────────────────────────────


@router.get(
    "/{project_id}/conflicts/pending",
    response_model=ApiResponse[list[PendingConflictSummary]],
    summary="List pending conflicts awaiting resolution",
)
async def list_pending_conflicts(
    project_id: str,
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return every pending conflict row with ``status='pending'``.

    Read-only access is sufficient — the resolver dashboard shows the
    queue to anyone who can see the project; only the resolve endpoint
    needs write access.
    """

    ensure_project_access(authorization, current_user, project_id)
    rows = _query_pending(project_id)
    summaries = [PendingConflictSummary(**_row_to_summary(row)) for row in rows]
    return ApiResponse.success(data=summaries)


@router.get(
    "/{project_id}/conflicts/{pending_conflict_id}",
    response_model=ApiResponse[PendingConflictDetail],
    summary="Inspect one pending conflict",
)
async def get_pending_conflict(
    project_id: str,
    pending_conflict_id: str,
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    ensure_project_access(authorization, current_user, project_id)
    row = _query_one(project_id, pending_conflict_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pending conflict not found")
    return ApiResponse.success(data=PendingConflictDetail(
        **_row_to_summary(row),
        conflict_records=row.get("conflict_records") or [],
        resolution_detail=row.get("resolution_detail") or {},
    ))


@router.post(
    "/{project_id}/conflicts/{pending_conflict_id}/resolve",
    response_model=ApiResponse[ResolveConflictResponse],
    summary="Resolve (accept or reject) a pending conflict",
)
async def resolve_pending_conflict(
    project_id: str,
    pending_conflict_id: str,
    body: ResolveConflictRequest,
    engine: VersionWriteEngine = Depends(get_version_write_engine),
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Drive ``VersionWriteEngine.resolve``.

    For ``accept`` decisions, ``resolution_tree_id`` or
    ``resolution_files`` must be present. The engine re-enters the
    publish pipeline against the *current* scope head, so a race with
    another write may itself land in ``pending`` — in that case the
    original row stays in ``resolving`` and the response carries the
    follow-up ``pending_conflict_id`` for the next round.
    """

    ensure_write_access(authorization, current_user, project_id)

    row = _query_one(project_id, pending_conflict_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pending conflict not found")
    if row.get("status") != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"pending conflict is already {row.get('status')!r}",
        )

    import base64
    resolution_files = None
    if body.resolution_files is not None:
        try:
            resolution_files = {
                path: base64.b64decode(b64)
                for path, b64 in body.resolution_files.items()
            }
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"resolution_files contains invalid base64: {exc}",
            )

    intent = ConflictResolutionIntent(
        project_id=project_id,
        pending_conflict_id=pending_conflict_id,
        scope_path=row.get("scope_path", "") or "",
        resolver_actor=f"user:{current_user.user_id}",
        source_channel="papi",
        resolution_tree_id=body.resolution_tree_id or "",
        resolution_files=resolution_files,
        resolution_message=body.resolution_message or "",
        decision=body.decision,
    )
    try:
        result = await engine.resolve(intent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"resolution failed: {exc}")

    return ApiResponse.success(data=ResolveConflictResponse(
        status=result.status,
        commit_id=result.commit_id or "",
        pending_conflict_id=pending_conflict_id,
        follow_up_pending_conflict_id=result.pending_conflict_id or "",
        reason=result.reason or "",
    ))


# ── Helpers ────────────────────────────────────────────────────


_SELECT_COLUMNS = (
    "pending_conflict_id, transaction_id, scope_path, base_commit_id, "
    "current_commit_id, client_commit_id, proposed_tree_id, policy, "
    "status, resolver_actor, resolver_kind, created_at, changed_paths, "
    "conflict_records, resolution_detail"
)


def _query_pending(project_id: str) -> list[dict]:
    client = SupabaseClient().client
    resp = (
        client.table(CONFLICTS_TABLE)
        .select(_SELECT_COLUMNS)
        .eq("project_id", project_id)
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(500)
        .execute()
    )
    return list(resp.data or [])


def _query_one(project_id: str, pending_conflict_id: str) -> dict | None:
    client = SupabaseClient().client
    resp = (
        client.table(CONFLICTS_TABLE)
        .select(_SELECT_COLUMNS)
        .eq("project_id", project_id)
        .eq("pending_conflict_id", pending_conflict_id)
        .maybe_single()
        .execute()
    )
    return getattr(resp, "data", None)


def _row_to_summary(row: dict) -> dict:
    return {
        "pending_conflict_id": row.get("pending_conflict_id", ""),
        "transaction_id": row.get("transaction_id"),
        "scope_path": row.get("scope_path", "") or "",
        "base_commit_id": row.get("base_commit_id", "") or "",
        "current_commit_id": row.get("current_commit_id", "") or "",
        "client_commit_id": row.get("client_commit_id", "") or "",
        "proposed_tree_id": row.get("proposed_tree_id", "") or "",
        "policy": row.get("policy", "") or "",
        "status": row.get("status", "") or "",
        "resolver_actor": row.get("resolver_actor", "") or "",
        "resolver_kind": row.get("resolver_kind", "") or "",
        "created_at": row.get("created_at", "") or "",
        "changed_paths": row.get("changed_paths") or [],
    }
