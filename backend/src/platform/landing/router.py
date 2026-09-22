from __future__ import annotations

import logging
import time
from collections import defaultdict

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile

from src.common_schemas import ApiResponse
from src.config import settings
from src.infra.s3.dependencies import get_s3_service
from src.infra.s3.service import S3Service
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.entitlements.service import EntitlementService
from src.platform.landing import tickets
from src.platform.landing.registry import get_tool_spec
from src.platform.landing.schemas import (
    ClaimRequest,
    ClaimResponse,
    PreviewResponse,
)
from src.platform.landing.service import LandingService
from src.platform.project.control_plane import ProjectControlPlaneService
from src.platform.project.control_plane_dependencies import get_project_control_plane_service
from src.version_engine.bootstrap.dependencies import get_version_write_engine
from src.version_engine.write_engine.engine import VersionWriteEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/landing", tags=["landing"])

# Hard ceiling on the anonymous, login-free preview path. The website should
# enforce a smaller limit + CAPTCHA + per-IP rate-limiting at its edge (M5).
MAX_PREVIEW_BYTES = 25 * 1024 * 1024


def get_landing_service(
    s3: S3Service = Depends(get_s3_service),
    control_plane: ProjectControlPlaneService = Depends(get_project_control_plane_service),
    entitlements: EntitlementService = Depends(get_entitlement_service),
    version_engine: VersionWriteEngine = Depends(get_version_write_engine),
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> LandingService:
    return LandingService(s3, control_plane, entitlements, version_engine, authorization)


def _check_proxy_secret(x_landing_secret: str | None) -> None:
    """Defense-in-depth: if LANDING_INGEST_SECRET is configured, the public
    preview endpoint only accepts calls that carry it — i.e. only the website's
    server-side proxy can reach it, never a raw browser. If unset (dev), the
    check is skipped."""
    expected = settings.LANDING_INGEST_SECRET
    if expected and x_landing_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid landing proxy secret")


def _client_ip(request: Request) -> str:
    """Real client IP. The website proxy forwards it as X-Forwarded-For; take
    the first hop. Falls back to the socket peer."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Per-IP sliding window (in-process; safe for a single deployment — swap for a
# Redis-backed counter if the backend is ever horizontally scaled).
_preview_hits: dict[str, list[float]] = defaultdict(list)
# Bound the table on a public endpoint: once it grows past this, drop IPs whose
# window has fully elapsed so unique-IP churn can't leak memory unbounded.
_MAX_TRACKED_IPS = 10_000


def _rate_limit_preview(ip: str) -> None:
    now = time.monotonic()
    window = settings.LANDING_PREVIEW_RATE_WINDOW
    if len(_preview_hits) > _MAX_TRACKED_IPS:
        stale = [k for k, v in _preview_hits.items() if not v or now - v[-1] >= window]
        for k in stale:
            del _preview_hits[k]
    _preview_hits[ip] = hits = [t for t in _preview_hits[ip] if now - t < window]
    if len(hits) >= settings.LANDING_PREVIEW_RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many previews. Please try again later.")
    hits.append(now)


async def _verify_turnstile(token: str | None, ip: str) -> None:
    """Verify a Cloudflare Turnstile token. No-op when TURNSTILE_SECRET is
    unset (dev). Fails closed (403) when configured but the token is missing
    or rejected."""
    secret = settings.TURNSTILE_SECRET
    if not secret:
        return
    if not token:
        raise HTTPException(status_code=403, detail="Captcha required")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": secret, "response": token, "remoteip": ip},
            )
        ok = bool(resp.json().get("success")) if resp.status_code == 200 else False
    except Exception:
        logger.warning("turnstile verification call failed", exc_info=True)
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Captcha verification failed")


@router.post(
    "/preview",
    response_model=ApiResponse[PreviewResponse],
    summary="Parse an uploaded source into a previewable MCP (login-free, no DB rows)",
)
async def landing_preview(
    request: Request,
    tool_kind: str = Form(..., description="Registry key, e.g. 'pdf'"),
    file: UploadFile = File(...),
    turnstile_token: str | None = Form(default=None),
    x_landing_secret: str | None = Header(default=None),
    service: LandingService = Depends(get_landing_service),
):
    _check_proxy_secret(x_landing_secret)
    ip = _client_ip(request)
    _rate_limit_preview(ip)
    await _verify_turnstile(turnstile_token, ip)
    try:
        get_tool_spec(tool_kind)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_PREVIEW_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(content)} bytes; preview cap is {MAX_PREVIEW_BYTES}.",
        )

    try:
        result = await service.preview(
            tool_kind=tool_kind,
            filename=file.filename or "file",
            content=content,
            content_type=file.content_type,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("landing preview failed for tool_kind=%s", tool_kind)
        raise HTTPException(status_code=502, detail="Preview failed") from exc

    return ApiResponse.success(data=result, message="Preview ready")


@router.post(
    "/claim",
    response_model=ApiResponse[ClaimResponse],
    summary="Turn a previewed ticket into a real, owned project + MCP endpoint",
)
async def landing_claim(
    payload: ClaimRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: LandingService = Depends(get_landing_service),
):
    try:
        result = await service.claim(
            ticket=payload.ticket,
            org_id=payload.org_id,
            user_id=current_user.user_id,
        )
    except tickets.TicketError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ticket: {exc}")
    return ApiResponse.success(data=result, message="Claimed")
