"""HTTP API for the managed sync policy (#M5).

GET /api/v1/scope-sync/policy → the resolved managed SyncPolicy for a scope
(role-aware), for the sidecar + frontend. Users don't configure triggers; this
returns the preset PuppyOne picked.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService
from src.platform.scope_sync.service import ScopeSyncService, get_scope_sync_service

router = APIRouter(prefix="/api/v1/scope-sync", tags=["scope-sync"])


def _authorize_project(
    authorization: AuthorizationService,
    current_user: CurrentUser,
    project_id: str,
    action: ProjectAction,
):
    return authorization.authorize(project_id, current_user.user_id, action)


@router.get("/policy", response_model=ApiResponse)
def get_policy(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    persona: str | None = Query(None, description="non_dev | dev | reviewer (default dev)"),
    client: str | None = Query(None, description="agent | vscode | cli | unknown"),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    _authorize_project(authorization, current_user, project_id, ProjectAction.CONTENT_READ)
    try:
        data = service.resolve_policy(
            project_id=project_id, scope_id=scope_id, persona=persona, client=client,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Scope not found in project")
    return ApiResponse.success(data=data)


@router.get("/events", response_model=ApiResponse)
def get_events(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    cursor: int = Query(0, description="last event id the client has seen"),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    """Path-scoped upstream events for a scope since ``cursor`` (M3). The sidecar
    polls this and integrates/holds per the managed policy."""
    _authorize_project(authorization, current_user, project_id, ProjectAction.CONTENT_READ)
    return ApiResponse.success(data=service.poll_events(
        project_id=project_id, scope_id=scope_id, cursor=cursor))


@router.get("/activity", response_model=ApiResponse)
def get_activity(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    """Recent sync activity (publish/projection event log) for a scope (M6)."""
    _authorize_project(authorization, current_user, project_id, ProjectAction.HISTORY_READ)
    return ApiResponse.success(data=service.activity(
        project_id=project_id, scope_id=scope_id, limit=limit))


@router.get("/stats", response_model=ApiResponse)
def get_stats(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    window: int = Query(200, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    """Aggregate sync observability for a scope (#8): publish volume, distinct
    origins, per-source breakdown, latest head — over the recent event window."""
    _authorize_project(authorization, current_user, project_id, ProjectAction.HISTORY_READ)
    return ApiResponse.success(data=service.stats(
        project_id=project_id, scope_id=scope_id, window=window))


@router.get("/ap/events", response_model=ApiResponse)
def ap_events(
    cursor: int = Query(0, description="last event id the sidecar has seen"),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    """Access-key-authenticated upstream events for the in-sandbox sidecar (no
    JWT). The sidecar passes the scope's access_key (which it already holds in
    its git remote). Scope + project are resolved from the key."""
    if not x_access_key:
        raise HTTPException(status_code=401, detail="X-Access-Key required")
    try:
        return ApiResponse.success(data=service.poll_events_by_access_key(
            access_key=x_access_key, cursor=cursor))
    except LookupError:
        raise HTTPException(status_code=403, detail="Invalid access key")


class SyncSettingsBody(BaseModel):
    project_id: str
    scope_id: str
    persona: str | None = None     # non_dev | dev | reviewer
    auto_sync: bool | None = None


@router.get("/settings", response_model=ApiResponse)
def get_settings(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    """The coarse, user-facing sync setting (persona + auto-sync) for a scope (M5)."""
    _authorize_project(authorization, current_user, project_id, ProjectAction.CONTENT_READ)
    return ApiResponse.success(data=service.get_settings(project_id=project_id, scope_id=scope_id))


@router.put("/settings", response_model=ApiResponse)
def put_settings(
    body: SyncSettingsBody,
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSyncService = Depends(get_scope_sync_service),
):
    _authorize_project(
        authorization, current_user, body.project_id, ProjectAction.SCOPE_MANAGE
    )
    if body.persona is not None and body.persona not in ("non_dev", "dev", "reviewer"):
        raise HTTPException(status_code=400, detail="persona must be non_dev | dev | reviewer")
    data = service.set_settings(project_id=body.project_id, scope_id=body.scope_id,
                                persona=body.persona, auto_sync=body.auto_sync)
    return ApiResponse.success(data=data, message="Sync settings updated")
