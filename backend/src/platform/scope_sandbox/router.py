"""HTTP API for the scope-sandbox "sandbox as access point" feature (#9).

Endpoints (JWT auth, project-scoped):
  POST /api/v1/scope-sandboxes/connect  → acquire/reuse the scope's sandbox,
        grant the caller a short-lived SSH key, return VSCode Remote-SSH info.
  GET  /api/v1/scope-sandboxes/status   → current session state for a scope.
  POST /api/v1/scope-sandboxes/revoke   → revoke the caller's SSH access.

The provider (Fly/E2B) is chosen per request (frontend selection), defaulting to
``settings.SCOPE_SANDBOX_PROVIDER``. All git/CLI runs inside the sandbox. Its
canonical Git locator is credential-free; a short-lived Git credential stays
server-side and reaches Git only through the sandbox credential-helper file.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.common_schemas import ApiResponse
from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService
from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.entitlements.service import EntitlementService
from src.platform.scope_sandbox.service import (
    ScopeSandboxService,
    get_scope_sandbox_service,
)
from src.utils.logger import log_warning

router = APIRouter(prefix="/api/v1/scope-sandboxes", tags=["scope-sandboxes"])


class ConnectRequest(BaseModel):
    project_id: str
    scope_id: str
    public_key: str = Field(..., description="The user's SSH public key (ed25519/rsa)")
    provider: str | None = Field(None, description="fly | e2b; defaults to server config")


class RevokeRequest(BaseModel):
    project_id: str
    scope_id: str


def _public_base(request: Request) -> str:
    base = (settings.PUBLIC_URL or "").strip()
    return base.rstrip("/") if base else str(request.base_url).rstrip("/")


def _user_name(current_user: CurrentUser) -> str:
    meta = current_user.user_metadata or {}
    return meta.get("name") or meta.get("full_name") or current_user.email or "puppyone"


def _guard_long_lived_runtime_metering() -> None:
    # Scope Sandbox is a long-lived SSH resource, unlike the bounded ephemeral
    # SandboxService sessions metered around start/stop. Until segmented leases
    # are implemented it must not create unreserved hosted cost in required mode.
    if settings.RUNTIME_METERING_MODE == "required":
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=503,
            message="Long-lived Scope Sandbox is not enabled for metered hosting",
            details={
                "code": "scope_sandbox_runtime_metering_unavailable",
                "retryable": False,
            },
        )
    if settings.RUNTIME_METERING_MODE == "shadow":
        log_warning("[runtime-billing] Scope Sandbox would be denied in required mode")


@router.post("/connect", response_model=ApiResponse)
async def connect(
    payload: ConnectRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    entitlement_service: EntitlementService = Depends(get_entitlement_service),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSandboxService = Depends(get_scope_sandbox_service),
):
    grant = authorization.authorize(
        payload.project_id, current_user.user_id, ProjectAction.SANDBOX_MANAGE
    )
    entitlement_service.require_feature(grant.org_id, "scope_sandbox.connect")
    _guard_long_lived_runtime_metering()
    if not payload.public_key.strip():
        raise HTTPException(status_code=400, detail="public_key is required")
    if payload.provider and payload.provider not in ("fly", "e2b"):
        raise HTTPException(status_code=400, detail="provider must be 'fly' or 'e2b'")
    try:
        info = await service.connect(
            project_id=payload.project_id,
            scope_id=payload.scope_id,
            user_id=current_user.user_id,
            user_email=current_user.email or "user@puppyone.ai",
            user_name=_user_name(current_user),
            public_key=payload.public_key.strip(),
            public_base=_public_base(request),
            provider_name=payload.provider,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Scope not found in project")
    return ApiResponse.success(data=asdict(info), message="Sandbox ready")


@router.get("/providers", response_model=ApiResponse)
def providers(
    current_user: CurrentUser = Depends(get_current_user),
    service: ScopeSandboxService = Depends(get_scope_sandbox_service),
):
    """Which sandbox providers this deployment offers + the default (for the
    frontend's provider selector). Auth required, but not project-scoped."""
    return ApiResponse.success(data=service.available_providers())


@router.get("/status", response_model=ApiResponse)
def status(
    project_id: str = Query(...),
    scope_id: str = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSandboxService = Depends(get_scope_sandbox_service),
):
    authorization.authorize(project_id, current_user.user_id, ProjectAction.ACCESS_READ)
    data = service.status(project_id=project_id, scope_id=scope_id, user_id=current_user.user_id)
    return ApiResponse.success(data=data)


@router.post("/revoke", response_model=ApiResponse)
async def revoke(
    payload: RevokeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    service: ScopeSandboxService = Depends(get_scope_sandbox_service),
):
    authorization.authorize(payload.project_id, current_user.user_id, ProjectAction.SANDBOX_MANAGE)
    remaining = await service.revoke(
        project_id=payload.project_id,
        scope_id=payload.scope_id,
        user_id=current_user.user_id,
    )
    return ApiResponse.success(data={"connected_users": remaining}, message="Access revoked")
