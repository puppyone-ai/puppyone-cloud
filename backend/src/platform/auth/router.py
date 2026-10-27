"""
Auth router — for CLI / external clients

POST   /auth/login           Sign in with email + password, returns access_token
POST   /auth/refresh         Refresh access_token
POST   /auth/logout          Revoke the refresh-token session (idempotent)
POST   /auth/initialize      Idempotent user initialization (profile + org)
POST   /auth/check-email     Resolve the existing email-first login flow
GET    /auth/config           Public Supabase config (URL + anon key) for Realtime
"""

import asyncio
import os
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from supabase import create_client

from src.common_schemas import ApiResponse
from src.config import settings
from src.platform.auth.dependencies import CurrentUser, get_current_user, get_initialization_service
from src.platform.auth.initialization import UserInitializationService
from src.platform.auth.methods import router as methods_router


router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(methods_router)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user_email: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: SecretStr = Field(min_length=1, max_length=8192)


class LogoutResponse(BaseModel):
    revoked: bool


class CheckEmailRequest(BaseModel):
    email: EmailStr


class CheckEmailResponse(BaseModel):
    exists: bool


def _make_auth_client():
    """Create a throwaway Supabase client for auth operations only.

    This avoids contaminating the global singleton's PostgREST session
    (sign_in_with_password stores the user token, which would cause all
    subsequent DB queries to run under RLS instead of service_role).
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    return create_client(url, key)


class RealtimeConfig(BaseModel):
    supabase_url: str
    supabase_anon_key: str


def _public_supabase_url() -> str:
    return (settings.SUPABASE_PUBLIC_URL or os.environ.get("SUPABASE_URL", "")).rstrip("/")


@router.get("/config", response_model=ApiResponse[RealtimeConfig])
def get_public_config():
    """Return public Supabase config needed by CLI for Realtime subscriptions."""
    url = _public_supabase_url()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not anon_key:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL or SUPABASE_ANON_KEY not configured on server",
        )
    return ApiResponse.success(
        data=RealtimeConfig(
            supabase_url=url,
            supabase_anon_key=anon_key,
        )
    )


@router.post("/check-email", response_model=ApiResponse[CheckEmailResponse])
async def check_email(
    body: CheckEmailRequest,
):
    """Preserve the existing email-first UI using Supabase's account lookup.

    No application-owned authentication state or Redis dependency is involved.
    """
    start = time.monotonic()

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase not configured")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/auth/v1/admin/users",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params={"filter": body.email, "page": 1, "per_page": 10},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Auth service unavailable")

            users = resp.json().get("users", [])
            target = body.email.lower()
            exists = any(u.get("email", "").lower() == target for u in users)

        # Pad response time to a constant floor so attackers can't infer
        # existence from faster/slower responses (timing side-channel).
        elapsed = time.monotonic() - start
        if elapsed < 0.4:
            await asyncio.sleep(0.4 - elapsed)

        return ApiResponse.success(data=CheckEmailResponse(exists=exists))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Check email failed: {e!s}")


@router.post("/login", response_model=ApiResponse[LoginResponse])
def login(body: LoginRequest):
    # Supabase owns authentication and its native rate limits.
    try:
        auth_client = _make_auth_client()
        result = auth_client.auth.sign_in_with_password(
            {
                "email": body.email,
                "password": body.password,
            }
        )

        session = result.session
        if not session:
            raise HTTPException(status_code=401, detail="Login failed: unable to create session")

        return ApiResponse.success(
            data=LoginResponse(
                access_token=session.access_token,
                refresh_token=session.refresh_token,
                expires_in=session.expires_in,
                user_email=result.user.email if result.user else body.email,
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        if getattr(e, "status", None) == 429:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Please try again later") from e
        error_msg = str(e)
        if "Invalid login" in error_msg or "invalid" in error_msg.lower():
            raise HTTPException(status_code=401, detail="Invalid email or password")
        raise HTTPException(status_code=401, detail=f"Login failed: {error_msg}")


@router.post("/refresh", response_model=ApiResponse[LoginResponse])
def refresh_token(body: RefreshRequest):
    # Supabase owns token validation, rotation and its native rate limits.
    try:
        auth_client = _make_auth_client()
        result = auth_client.auth.refresh_session(body.refresh_token)

        session = result.session
        if not session:
            raise HTTPException(status_code=401, detail="Refresh failed: invalid session")

        return ApiResponse.success(
            data=LoginResponse(
                access_token=session.access_token,
                refresh_token=session.refresh_token,
                expires_in=session.expires_in,
                user_email=result.user.email if result.user else "",
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        if getattr(e, "status", None) == 429:
            raise HTTPException(status_code=429, detail="Too many refresh attempts. Please try again later") from e
        raise HTTPException(status_code=401, detail=f"Refresh failed: {e!s}")


@router.post("/logout", response_model=ApiResponse[LogoutResponse])
async def logout(body: LogoutRequest):
    """Revoke the Supabase session identified by a refresh token.

    Desktop can hold a refresh token after its access JWT expires, while the
    upstream logout endpoint requires a current access JWT. Rotate the supplied
    refresh token once, then revoke that exact session with ``scope=local``.
    Invalid/already-revoked tokens are an idempotent success; provider outages
    remain visible so the client can report remote-revoke failure while still
    completing its unconditional local logout.
    """
    refresh_value = body.refresh_token.get_secret_value().strip()
    if not refresh_value:
        raise HTTPException(status_code=400, detail="Refresh token is required")

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    api_key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_KEY", "")
    if not supabase_url or not api_key:
        raise HTTPException(status_code=503, detail="Authentication provider is not configured")

    provider_headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            refreshed = await client.post(
                f"{supabase_url}/auth/v1/token?grant_type=refresh_token",
                headers=provider_headers,
                json={"refresh_token": refresh_value},
            )
            if refreshed.status_code in {400, 401, 403}:
                return ApiResponse.success(data=LogoutResponse(revoked=True))
            if refreshed.status_code != 200:
                raise HTTPException(status_code=502, detail="Authentication provider unavailable")
            try:
                access_token = str(refreshed.json().get("access_token", "")).strip()
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Authentication provider returned an invalid session",
                ) from exc
            if not access_token:
                raise HTTPException(
                    status_code=502,
                    detail="Authentication provider returned an invalid session",
                )

            revoked = await client.post(
                f"{supabase_url}/auth/v1/logout?scope=local",
                headers={
                    **provider_headers,
                    "Authorization": f"Bearer {access_token}",
                },
            )
            if not (200 <= revoked.status_code < 300) and revoked.status_code not in {
                401,
                403,
            }:
                raise HTTPException(status_code=502, detail="Authentication provider unavailable")
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Authentication provider unavailable") from exc

    return ApiResponse.success(data=LogoutResponse(revoked=True))


class InitializeResponse(BaseModel):
    org_id: str
    is_new_org: bool
    demo_project_id: str | None = None


@router.post("/initialize", response_model=ApiResponse[InitializeResponse])
async def initialize_user(
    current_user: CurrentUser = Depends(get_current_user),
    init_service: UserInitializationService = Depends(get_initialization_service),
):
    """Idempotent user initialization: ensures profile + default org +
    membership exist, and on first sign-in seeds a "Get Started" demo
    project so the post-login redirect can land the user inside it
    instead of an empty dashboard."""
    result = init_service.ensure_initialized(
        user_id=current_user.user_id,
        email=current_user.email,
        display_name=current_user.user_metadata.get("full_name")
        if current_user.user_metadata
        else None,
    )
    if settings.ENTITLEMENTS_MODE == "db":
        from src.platform.billing.provisioning import get_entitlement_provisioning_service

        await get_entitlement_provisioning_service().ensure(
            org_id=result["org_id"],
            actor_user_id=current_user.user_id,
        )
    demo_project_id = await init_service.maybe_seed_demo_project(
        user_id=current_user.user_id,
        org_id=result["org_id"],
    )
    return ApiResponse.success(
        data=InitializeResponse(
            org_id=result["org_id"],
            is_new_org=result["is_new_org"],
            demo_project_id=demo_project_id,
        )
    )
