"""Provider-independent Desktop login and atomic, proof-bound session handoff."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from urllib.parse import urlencode, urlparse

from src.config import settings
from src.platform.auth.desktop_models import DesktopStartRequest
from src.platform.auth.shared_security_store import AtomicTTLStore

PKCE_VALUE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


class DesktopAuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def invalid_request() -> DesktopAuthError:
    return DesktopAuthError(
        "auth_request_expired", "Sign-in request is invalid or expired. Start sign-in again."
    )


def challenge_for(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def browser_login_url(state: str, provider: str | None = None) -> str:
    origin = settings.FRONTEND_URL.rstrip("/")
    parsed = urlparse(origin)
    local = settings.APP_ENV in {"test", "development"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise DesktopAuthError("auth_unavailable", "Desktop browser login is not configured", 503)
    query = {"client": "desktop", "desktop_state": state}
    if provider:
        query["provider"] = provider
    return f"{origin}/login?{urlencode(query)}"


def validate_callback(callback: str) -> str:
    parsed = urlparse(callback)
    allowed = {
        value.strip()
        for value in settings.DESKTOP_AUTH_ALLOWED_CALLBACKS.split(",")
        if value.strip()
    }
    try:
        loopback = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.port is not None
            and 0 < parsed.port <= 65535
            and parsed.path == "/auth/callback"
        )
    except ValueError:
        loopback = False
    if (
        parsed.query
        or parsed.fragment
        or parsed.params
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.scheme
        or not (loopback or callback in allowed)
    ):
        raise DesktopAuthError("auth_callback_invalid", "Desktop callback URL is not allowed")
    return callback


class DesktopAuthService:
    def __init__(self, store: AtomicTTLStore):
        self.store = store

    def start(self, body: DesktopStartRequest) -> dict:
        provider = (body.provider or "").strip().lower() or None
        if provider not in {None, "google", "github"}:
            raise DesktopAuthError("auth_provider_unsupported", "Unsupported OAuth provider")
        challenge = (body.code_challenge or "").strip()
        if (body.code_challenge_method or "").upper() != "S256" or not PKCE_VALUE.fullmatch(
            challenge
        ):
            raise DesktopAuthError("auth_pkce_invalid", "Invalid Desktop PKCE challenge")
        callback = validate_callback(body.callback_url)
        state = secrets.token_urlsafe(32)
        login_url = browser_login_url(state, provider)
        self.store.put(
            "desktop-state",
            state,
            {
                "flow_version": 2,
                "callback_url": callback,
                "desktop_code_challenge": challenge,
            },
            settings.DESKTOP_AUTH_STATE_TTL_SECONDS,
        )
        return {"state": state, "login_url": login_url}

    def pending(self, state: str) -> dict:
        record = self.store.read("desktop-state", state)
        if not record or not record.get("desktop_code_challenge"):
            raise invalid_request()
        return record

    def bind(self, state: str, proof: str) -> None:
        if not PKCE_VALUE.fullmatch(proof):
            raise invalid_request()
        pending = self.pending(state)
        digest = challenge_for(proof)
        existing = pending.get("browser_binding")
        if existing:
            if not secrets.compare_digest(existing, digest):
                raise invalid_request()
            return
        # A same-browser retry is harmless, while a competing browser fails.
        if (
            not self.store.transition(
                "desktop-state", state, pending, replacement={**pending, "browser_binding": digest}
            )
            and self.pending(state).get("browser_binding") != digest
        ):
            raise invalid_request()

    def pending_for_browser(self, state: str, proof: str | None) -> dict:
        pending = self.pending(state)
        binding = pending.get("browser_binding")
        if (pending.get("flow_version") == 2 or binding) and (
            not proof
            or not PKCE_VALUE.fullmatch(proof)
            or not binding
            or not secrets.compare_digest(binding, challenge_for(proof))
        ):
            raise invalid_request()
        return pending

    def complete(self, state: str, pending: dict, session: dict) -> str:
        code = secrets.token_urlsafe(32)
        record = {
            "state": state,
            "session": session,
            "callback_url": pending["callback_url"],
            "desktop_code_challenge": pending["desktop_code_challenge"],
        }
        if not self.store.transition(
            "desktop-state",
            state,
            pending,
            destination=(
                "desktop-exchange",
                code,
                record,
                settings.DESKTOP_AUTH_EXCHANGE_TTL_SECONDS,
            ),
        ):
            raise invalid_request()
        return f"{pending['callback_url']}?{urlencode({'code': code, 'state': state})}"

    def exchange(self, code: str, state: str, verifier: str, redirect: str | None) -> dict:
        record = self.store.read("desktop-exchange", code)
        if (
            not record
            or not PKCE_VALUE.fullmatch(verifier)
            or not secrets.compare_digest(str(record.get("state", "")), state)
            or not secrets.compare_digest(
                str(record.get("desktop_code_challenge", "")), challenge_for(verifier)
            )
            or not redirect
            or not secrets.compare_digest(str(record.get("callback_url", "")), redirect)
        ):
            raise invalid_request()
        if not self.store.transition("desktop-exchange", code, record):
            raise invalid_request()
        return record["session"]
