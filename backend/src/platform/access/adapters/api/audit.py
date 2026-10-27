"""
Audit Logs — API Router

Endpoints:
  GET  /nodes/{path:path}/audit-logs            node audit logs
  GET  /nodes/project-audit-logs                project-level audit logs
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.common_schemas import ApiResponse
from src.infra.supabase.client import SupabaseClient
from src.version_engine.infrastructure.supabase.audit_repository import AuditRepository
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService

router = APIRouter(prefix="/nodes", tags=["audit-logs"])


# ============================================================
# Response Schemas
# ============================================================

class AuditLogItem(BaseModel):
    id: int
    action: str
    path: str | None = None
    operator_type: str
    operator_id: str | None = None
    status: str | None = None
    strategy: str | None = None
    conflict_details: str | None = None
    metadata: dict | None = None
    created_at: datetime | None = None
    # V1 typed columns (migration 20260516010000): the new RPC populates
    # these directly so the activity feed can filter / join without
    # re-parsing ``metadata`` JSONB. Historical rows have nulls except
    # where the Round 5 J1 backfill could derive a value.
    transaction_id: int | None = None
    canonical_commit_id: str | None = None
    original_commit_id: str | None = None
    project_view_commit_id: str | None = None
    scope_view_commit_id: str | None = None
    scope_path: str | None = None
    source_channel: str | None = None
    policy: str | None = None


class AuditLogListResponse(BaseModel):
    path: str
    logs: list[AuditLogItem]
    total: int


class ProjectAuditLogListResponse(BaseModel):
    logs: list[AuditLogItem]
    total: int


# ============================================================
# Dependencies
# ============================================================

def _get_audit_repo() -> AuditRepository:
    return AuditRepository(SupabaseClient())


def _ensure_project_access(
    authorization: AuthorizationService, current_user: CurrentUser, project_id: str
):
    """Authorize audit/history read through the canonical Project PDP."""
    authorization.authorize(
        project_id, current_user.user_id, ProjectAction.HISTORY_READ
    )


# ============================================================
# Endpoints
# ============================================================

@router.get("/project-audit-logs", response_model=ApiResponse[ProjectAuditLogListResponse])
def get_project_audit_logs(
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    audit_repo: AuditRepository = Depends(_get_audit_repo),
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all audit logs for a project (clone/push/pull/rollback events)"""
    _ensure_project_access(authorization, current_user, project_id)

    rows = audit_repo.list_by_project(project_id, limit, offset)
    total = audit_repo.count_by_project(project_id)
    logs = [AuditLogItem(**row) for row in rows]

    return ApiResponse.success(data=ProjectAuditLogListResponse(
        logs=logs,
        total=total,
    ))


@router.get("/{path:path}/audit-logs", response_model=ApiResponse[AuditLogListResponse])
def get_node_audit_logs(
    path: str,
    project_id: str = Query(..., description="Project ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    audit_repo: AuditRepository = Depends(_get_audit_repo),
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get audit logs for a node"""
    _ensure_project_access(authorization, current_user, project_id)

    rows = audit_repo.list_by_path(path, limit, offset, project_id=project_id)
    total = audit_repo.count_by_path(path, project_id=project_id)

    logs = [AuditLogItem(**row) for row in rows]

    return ApiResponse.success(data=AuditLogListResponse(
        path=path,
        logs=logs,
        total=total,
    ))
