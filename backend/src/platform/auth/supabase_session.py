"""Supabase adapter for validating and rotating a session before native handoff."""

import os

import httpx
from starlette.concurrency import run_in_threadpool

from src.platform.auth.desktop_service import DesktopAuthError


class SupabaseSessionVerifier:
    """Confirm the access/refresh pair with Supabase before passing ownership.

    Refresh rotation proves possession of the session, not just a copied user
    identifier. Returned tokens, rather than stale browser tokens, are handed off.
    """

    def __init__(self, auth_service):
        self.auth_service = auth_service

    async def verify_pair(self, access: str, refresh: str) -> dict:
        try:
            original = await run_in_threadpool(self.auth_service.verify_token, access)
        except Exception as exc:
            raise DesktopAuthError(
                "auth_session_invalid", "Sign-in session is invalid. Sign in again.", 401
            ) from exc
        if original.is_anonymous or original.role != "authenticated" or not original.session_id:
            raise DesktopAuthError(
                "auth_session_invalid", "Sign-in session is invalid. Sign in again.", 401
            )
        session = await self.token_request("refresh_token", {"refresh_token": refresh})
        try:
            refreshed = await run_in_threadpool(
                self.auth_service.verify_token, session["access_token"]
            )
            if (
                refreshed.user_id != original.user_id
                or refreshed.session_id != original.session_id
                or session.get("user", {}).get("id") != original.user_id
                or refreshed.is_anonymous
                or refreshed.role != "authenticated"
            ):
                raise ValueError("Session mismatch")
        except Exception as exc:
            raise DesktopAuthError(
                "auth_session_mismatch",
                "Sign-in credentials do not belong to the same session.",
                401,
            ) from exc
        return {**session, "user_id": refreshed.user_id, "user_email": refreshed.email or ""}

    async def token_request(self, grant: str, payload: dict) -> dict:
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            raise DesktopAuthError(
                "auth_unavailable", "Authentication provider is not configured", 503
            )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{url}/auth/v1/token",
                    params={"grant_type": grant},
                    headers={"apikey": key, "Content-Type": "application/json"},
                    json=payload,
                )
            if response.status_code in {400, 401, 403}:
                raise DesktopAuthError(
                    "auth_session_invalid", "Sign-in session is invalid. Sign in again.", 401
                )
            if response.status_code != 200:
                raise DesktopAuthError(
                    "auth_unavailable", "Authentication provider is temporarily unavailable", 502
                )
            session = response.json()
            if (
                not isinstance(session, dict)
                or not session.get("access_token")
                or not session.get("refresh_token")
            ):
                raise ValueError("Invalid provider session")
            # Provider tokens grant external data access, not Puppyone identity.
            return {
                key: session[key]
                for key in (
                    "access_token",
                    "refresh_token",
                    "expires_in",
                    "expires_at",
                    "token_type",
                    "user",
                )
                if key in session
            }
        except (httpx.HTTPError, ValueError) as exc:
            raise DesktopAuthError(
                "auth_unavailable", "Authentication provider is temporarily unavailable", 502
            ) from exc
