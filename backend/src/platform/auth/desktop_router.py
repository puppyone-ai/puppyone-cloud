"""HTTP adapter for the common Desktop authentication service."""

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import get_auth_service
from src.platform.auth.desktop_models import (
    DesktopBindRequest,
    DesktopCompleteRequest,
    DesktopCompleteResponse,
    DesktopExchangeRequest,
    DesktopStartRequest,
    DesktopStartResponse,
)
from src.platform.auth.desktop_service import (
    DesktopAuthError,
    DesktopAuthService,
    invalid_request,
)
from src.platform.auth.shared_security_store import get_auth_security_store
from src.platform.auth.supabase_session import SupabaseSessionVerifier

router = APIRouter(prefix="/desktop")


def get_desktop_service(store=Depends(get_auth_security_store)):
    return DesktopAuthService(store)


def get_session_verifier(auth_service=Depends(get_auth_service)):
    return SupabaseSessionVerifier(auth_service)


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def reject(exc: DesktopAuthError):
    raise HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
        headers={"Cache-Control": "private, no-store"},
    ) from exc


def throttle(request: Request, service: DesktopAuthService, operation: str):
    # Trusted edge forwarding is configured centrally; never trust arbitrary XFF.
    subject = hashlib.sha256(
        (request.client.host if request.client else "unknown").encode()
    ).hexdigest()
    if service.store.hit("desktop-" + operation, subject, 60, 60):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again shortly.",
            headers={"Retry-After": "60"},
        )


@router.post("/start", response_model=ApiResponse[DesktopStartResponse])
def start(
    body: DesktopStartRequest,
    request: Request,
    response: Response,
    service=Depends(get_desktop_service),
):
    no_store(response)
    throttle(request, service, "start")
    try:
        return ApiResponse.success(data=service.start(body))
    except DesktopAuthError as exc:
        reject(exc)


@router.post("/bind")
def bind(
    body: DesktopBindRequest,
    request: Request,
    response: Response,
    service=Depends(get_desktop_service),
):
    no_store(response)
    throttle(request, service, "bind")
    try:
        service.bind(body.state, body.browser_proof.get_secret_value())
        return ApiResponse.success(data={"bound": True})
    except DesktopAuthError as exc:
        reject(exc)


@router.post("/complete", response_model=ApiResponse[DesktopCompleteResponse])
async def complete(
    body: DesktopCompleteRequest,
    request: Request,
    response: Response,
    service=Depends(get_desktop_service),
    verifier=Depends(get_session_verifier),
):
    no_store(response)
    await run_in_threadpool(throttle, request, service, "complete")
    access = body.access_token.get_secret_value().strip()
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(access, bearer.strip()):
        raise HTTPException(status_code=401, detail="Sign-in session is invalid")
    try:
        pending = await run_in_threadpool(
            service.pending_for_browser,
            body.state,
            body.browser_proof.get_secret_value() if body.browser_proof else None,
        )
        session = await verifier.verify_pair(access, body.refresh_token.get_secret_value())
        redirect_url = await run_in_threadpool(service.complete, body.state, pending, session)
        return ApiResponse.success(data={"redirect_url": redirect_url})
    except DesktopAuthError as exc:
        reject(exc)


@router.post("/exchange")
def exchange(
    body: DesktopExchangeRequest,
    request: Request,
    response: Response,
    service=Depends(get_desktop_service),
):
    no_store(response)
    throttle(request, service, "exchange")
    try:
        return ApiResponse.success(
            data=service.exchange(
                body.code,
                body.state,
                body.code_verifier.get_secret_value() if body.code_verifier else "",
                body.redirect_uri,
            )
        )
    except DesktopAuthError as exc:
        reject(exc)


@router.get("/callback")
async def legacy_callback(
    code: str,
    state: str,
    service=Depends(get_desktop_service),
    verifier=Depends(get_session_verifier),
):
    """Drain already-issued legacy OAuth requests; new starts use Web login."""
    try:
        pending = await run_in_threadpool(service.pending, state)
        if not pending.get("code_verifier") or pending.get("flow_version") == 2:
            raise invalid_request()
        session = await verifier.token_request(
            "pkce", {"auth_code": code, "code_verifier": pending["code_verifier"]}
        )
        session = await verifier.verify_pair(session["access_token"], session["refresh_token"])
        redirect_url = await run_in_threadpool(service.complete, state, pending, session)
        return RedirectResponse(
            redirect_url,
            status_code=302,
            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
        )
    except DesktopAuthError as exc:
        reject(exc)
