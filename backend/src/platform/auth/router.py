"""
Auth router — for CLI / external clients

POST   /auth/login           Sign in with email + password, returns access_token
POST   /auth/refresh         Refresh access_token
POST   /auth/logout          Revoke the refresh-token session (idempotent)
POST   /auth/initialize      Idempotent user initialization (profile + org)
POST   /auth/check-email     Check if email is already registered (rate-limited)
GET    /auth/config           Public Supabase config (URL + anon key) for Realtime
"""

import asyncio
import base64
import hashlib
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from supabase import create_client

from src.common_schemas import ApiResponse
from src.config import settings
from src.platform.auth.dependencies import CurrentUser, get_current_user, get_initialization_service
from src.platform.auth.initialization import UserInitializationService
from src.platform.auth.shared_security_store import (
    AtomicTTLStore,
    RateLimiter,
    SecurityStoreUnavailable,
    get_auth_security_store,
)

router = APIRouter(prefix="/auth", tags=["auth"])


_CHECK_EMAIL_WINDOW = 60  # seconds
_CHECK_EMAIL_MAX_HITS = 5  # max requests per window per IP
_CHECK_EMAIL_MIN_LATENCY = 0.4  # seconds — flatten timing side-channel
_LOGIN_WINDOW = 600
_LOGIN_MAX_HITS = 10


def _hash_key(value: str) -> str:
    # Hash the account so Redis never stores plaintext emails, and keys are bounded.
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:24]


def _enforce_rate_limit(
    limiter: RateLimiter,
    *,
    bucket: str,
    subject: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        exceeded = limiter.hit(bucket, _hash_key(subject), limit, window_seconds)
    except SecurityStoreUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Authentication security store unavailable"
        ) from exc
    if exceeded:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(window_seconds)},
        )


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


class DesktopStartRequest(BaseModel):
    provider: str | None = None
    callback_url: str
    code_challenge: str | None = None
    code_challenge_method: str | None = None


class DesktopStartResponse(BaseModel):
    state: str
    login_url: str


class DesktopExchangeRequest(BaseModel):
    code: str
    state: str
    code_verifier: str | None = None
    redirect_uri: str | None = None


class DesktopCompleteRequest(BaseModel):
    state: str
    access_token: str
    refresh_token: str
    expires_in: int | None = None
    user_email: str | None = None


class DesktopCompleteResponse(BaseModel):
    redirect_url: str


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


def _allowed_desktop_callbacks() -> set[str]:
    return {
        item.strip() for item in settings.DESKTOP_AUTH_ALLOWED_CALLBACKS.split(",") if item.strip()
    }


def _public_supabase_url() -> str:
    """Return the browser-reachable auth origin, not an internal service DNS name."""
    return (settings.SUPABASE_PUBLIC_URL or os.environ.get("SUPABASE_URL", "")).rstrip("/")


_DESKTOP_PKCE_VALUE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_DESKTOP_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
_DESKTOP_LOOPBACK_PATH = "/auth/callback"


def _validate_desktop_code_challenge(
    code_challenge: str | None,
    code_challenge_method: str | None,
) -> str:
    challenge = (code_challenge or "").strip()
    method = (code_challenge_method or "").strip().upper()
    if method != "S256" or not _DESKTOP_PKCE_VALUE.fullmatch(challenge):
        raise HTTPException(status_code=400, detail="Invalid Desktop PKCE challenge")
    return challenge


def _is_desktop_loopback_callback(callback_url: str) -> bool:
    parsed = urlparse(callback_url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in _DESKTOP_LOOPBACK_HOSTS
        and port is not None
        and parsed.path == _DESKTOP_LOOPBACK_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def _validate_desktop_callback(callback_url: str, *, allow_loopback: bool = False) -> str:
    parsed = urlparse(callback_url)
    if parsed.query or parsed.fragment or not parsed.scheme:
        raise HTTPException(status_code=400, detail="Invalid desktop callback URL")
    if callback_url in _allowed_desktop_callbacks():
        return callback_url
    if allow_loopback and _is_desktop_loopback_callback(callback_url):
        return callback_url
    raise HTTPException(status_code=400, detail="Desktop callback URL is not allowed")


def _store_error(exc: SecurityStoreUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail="Authentication security store unavailable")


def _desktop_browser_login_url(state: str) -> str:
    frontend_origin = settings.FRONTEND_URL.rstrip("/")
    parsed = urlparse(frontend_origin)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not frontend_origin
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (
            parsed.scheme != "https"
            and not (
                settings.APP_ENV in {"development", "test"} and parsed.scheme == "http" and loopback
            )
        )
    ):
        raise HTTPException(status_code=503, detail="Desktop browser login is not configured")
    query = urlencode({"client": "desktop", "desktop_state": state})
    return f"{frontend_origin}/login?{query}"


def _desktop_exchange_redirect(
    *,
    state: str,
    pending: dict,
    session: dict,
    store: AtomicTTLStore,
) -> str:
    exchange_code = secrets.token_urlsafe(32)
    try:
        store.put(
            "desktop-exchange",
            exchange_code,
            {
                "state": state,
                "session": session,
                "callback_url": pending["callback_url"],
                "desktop_code_challenge": pending.get("desktop_code_challenge"),
            },
            settings.DESKTOP_AUTH_EXCHANGE_TTL_SECONDS,
        )
    except SecurityStoreUnavailable as exc:
        raise _store_error(exc) from exc
    callback_url = str(pending["callback_url"])
    separator = "&" if "?" in callback_url else "?"
    return f"{callback_url}{separator}{urlencode({'code': exchange_code, 'state': state})}"


def _request_bearer_token(request: Request) -> str:
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if separator and scheme.lower() == "bearer" and token.strip():
        return token.strip()
    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


@router.post("/desktop/start", response_model=ApiResponse[DesktopStartResponse])
def desktop_auth_start(
    body: DesktopStartRequest,
    store: AtomicTTLStore = Depends(get_auth_security_store),
):
    """Start a browser-mediated Desktop flow whose state is replica-safe."""
    provider = (body.provider or "").strip().lower() or None
    if provider is not None and provider not in {"google", "github"}:
        raise HTTPException(status_code=400, detail="Unsupported OAuth provider")
    desktop_code_challenge = _validate_desktop_code_challenge(
        body.code_challenge,
        body.code_challenge_method,
    )
    callback_url = _validate_desktop_callback(
        body.callback_url,
        allow_loopback=True,
    )
    state = secrets.token_urlsafe(32)
    pending = {
        "callback_url": callback_url,
        "desktop_code_challenge": desktop_code_challenge,
    }

    if provider is None:
        login_url = _desktop_browser_login_url(state)
    else:
        public_base = settings.DESKTOP_AUTH_PUBLIC_BASE_URL.rstrip("/")
        supabase_url = _public_supabase_url()
        if not public_base or not supabase_url:
            raise HTTPException(status_code=503, detail="Desktop OAuth is not configured")
        verifier = secrets.token_urlsafe(64)
        provider_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        pending["code_verifier"] = verifier
        redirect_to = f"{public_base}/auth/desktop/callback?{urlencode({'state': state})}"
        authorize_params = urlencode(
            {
                "provider": provider,
                "redirect_to": redirect_to,
                "code_challenge": provider_challenge,
                "code_challenge_method": "s256",
            }
        )
        login_url = f"{supabase_url}/auth/v1/authorize?{authorize_params}"

    try:
        store.put(
            "desktop-state",
            state,
            pending,
            settings.DESKTOP_AUTH_STATE_TTL_SECONDS,
        )
    except SecurityStoreUnavailable as exc:
        raise _store_error(exc) from exc
    return ApiResponse.success(data=DesktopStartResponse(state=state, login_url=login_url))


@router.get("/desktop/callback")
async def desktop_auth_callback(
    code: str,
    state: str,
    store: AtomicTTLStore = Depends(get_auth_security_store),
):
    """Consume OAuth state, exchange PKCE code, then issue a one-time desktop code."""
    try:
        pending = store.consume("desktop-state", state)
    except SecurityStoreUnavailable as exc:
        raise _store_error(exc) from exc
    if not pending:
        raise HTTPException(status_code=400, detail="Desktop OAuth state is invalid or expired")
    provider_verifier = pending.get("code_verifier")
    if not provider_verifier:
        raise HTTPException(status_code=400, detail="Desktop OAuth state is invalid or expired")

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_KEY", "")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=pkce",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"auth_code": code, "code_verifier": provider_verifier},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="OAuth code exchange failed")
    session = response.json()
    if not session.get("access_token") or not session.get("refresh_token"):
        raise HTTPException(status_code=502, detail="OAuth provider returned an invalid session")

    return RedirectResponse(
        _desktop_exchange_redirect(
            state=state,
            pending=pending,
            session=session,
            store=store,
        ),
        status_code=302,
    )


@router.post("/desktop/complete", response_model=ApiResponse[DesktopCompleteResponse])
def desktop_auth_complete(
    body: DesktopCompleteRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    store: AtomicTTLStore = Depends(get_auth_security_store),
):
    """Bind a verified browser session to the pending native PKCE flow."""
    bearer_token = _request_bearer_token(request)
    access_token = body.access_token.strip()
    refresh_token = body.refresh_token.strip()
    if (
        current_user.is_anonymous
        or not access_token
        or not refresh_token
        or not secrets.compare_digest(bearer_token, access_token)
    ):
        raise HTTPException(status_code=401, detail="Desktop browser session is invalid")
    if (
        body.user_email
        and current_user.email
        and body.user_email.strip().lower() != current_user.email.strip().lower()
    ):
        raise HTTPException(status_code=401, detail="Desktop browser session is invalid")

    try:
        pending = store.consume("desktop-state", body.state)
    except SecurityStoreUnavailable as exc:
        raise _store_error(exc) from exc
    if not pending or not pending.get("desktop_code_challenge"):
        raise HTTPException(status_code=400, detail="Desktop OAuth state is invalid or expired")

    session = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": max(0, int(body.expires_in or 0)),
        "user_id": current_user.user_id,
        "user_email": current_user.email or body.user_email or "",
        "user": {
            "id": current_user.user_id,
            "email": current_user.email or body.user_email,
        },
    }
    redirect_url = _desktop_exchange_redirect(
        state=body.state,
        pending=pending,
        session=session,
        store=store,
    )
    return ApiResponse.success(data=DesktopCompleteResponse(redirect_url=redirect_url))


@router.post("/desktop/exchange")
def desktop_auth_exchange(
    body: DesktopExchangeRequest,
    store: AtomicTTLStore = Depends(get_auth_security_store),
):
    try:
        record = store.consume("desktop-exchange", body.code)
    except SecurityStoreUnavailable as exc:
        raise _store_error(exc) from exc
    if not record or not secrets.compare_digest(str(record.get("state", "")), body.state):
        raise HTTPException(status_code=400, detail="Desktop exchange code is invalid or expired")
    desktop_code_challenge = record.get("desktop_code_challenge")
    if desktop_code_challenge:
        verifier = (body.code_verifier or "").strip()
        callback_url = str(record.get("callback_url", ""))
        if not _DESKTOP_PKCE_VALUE.fullmatch(verifier):
            raise HTTPException(
                status_code=400, detail="Desktop exchange code is invalid or expired"
            )
        candidate_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        if (
            not secrets.compare_digest(candidate_challenge, str(desktop_code_challenge))
            or not body.redirect_uri
            or not secrets.compare_digest(body.redirect_uri, callback_url)
        ):
            raise HTTPException(
                status_code=400, detail="Desktop exchange code is invalid or expired"
            )
    return ApiResponse.success(data=record["session"])


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
    request: Request,
    limiter: RateLimiter = Depends(get_auth_security_store),
):
    """Check whether an email is already registered (for email-first login flow).

    Protected by per-IP rate limiting and constant-time response delay
    to mitigate email enumeration attacks.
    """
    client_ip = request.client.host if request.client else "unknown"
    _enforce_rate_limit(
        limiter,
        bucket="check-email",
        subject=client_ip,
        limit=_CHECK_EMAIL_MAX_HITS,
        window_seconds=_CHECK_EMAIL_WINDOW,
    )

    start = time.monotonic()

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase not configured")

    try:
        async with httpx.AsyncClient() as client:
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
        if elapsed < _CHECK_EMAIL_MIN_LATENCY:
            await asyncio.sleep(_CHECK_EMAIL_MIN_LATENCY - elapsed)

        return ApiResponse.success(data=CheckEmailResponse(exists=exists))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Check email failed: {e!s}")


@router.post("/login", response_model=ApiResponse[LoginResponse])
def login(
    body: LoginRequest,
    limiter: RateLimiter = Depends(get_auth_security_store),
):
    _enforce_rate_limit(
        limiter,
        bucket="login",
        subject=(body.email or "").strip().lower() or "unknown",
        limit=_LOGIN_MAX_HITS,
        window_seconds=_LOGIN_WINDOW,
    )
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
        error_msg = str(e)
        if "Invalid login" in error_msg or "invalid" in error_msg.lower():
            raise HTTPException(status_code=401, detail="Invalid email or password")
        raise HTTPException(status_code=401, detail=f"Login failed: {error_msg}")


@router.post("/refresh", response_model=ApiResponse[LoginResponse])
def refresh_token(body: RefreshRequest):
    # No rate limit here: a refresh requires possession of a high-entropy refresh
    # token (not brute-forceable), and limiting it risks throttling legitimate
    # clients. Login (password guessing) is the real brute-force surface.
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
