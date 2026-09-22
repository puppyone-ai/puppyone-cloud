from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.exception_handler import security_store_unavailable_handler
from src.platform.auth import router as auth_router
from src.platform.auth.models import CurrentUser
from src.platform.auth.shared_security_store import SecurityStoreUnavailable

CALLBACK_URL = "puppyone://auth/callback"
LOOPBACK_CALLBACK_URL = "http://127.0.0.1:43123/auth/callback"
DESKTOP_VERIFIER = "desktop-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFG"
DESKTOP_CHALLENGE = base64.urlsafe_b64encode(
    hashlib.sha256(DESKTOP_VERIFIER.encode("ascii")).digest()
).rstrip(b"=").decode("ascii")


class _MemorySecurityStore:
    """Minimal atomic TTL-store fake for exercising the replica-safe flow."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], dict] = {}
        self.puts: list[tuple[str, str, dict, int]] = []

    def put(self, namespace: str, key: str, value: dict, ttl_seconds: int) -> None:
        self.values[(namespace, key)] = dict(value)
        self.puts.append((namespace, key, dict(value), ttl_seconds))

    def consume(self, namespace: str, key: str) -> dict | None:
        return self.values.pop((namespace, key), None)

    def hit(self, bucket: str, subject: str, limit: int, window_seconds: int) -> bool:
        return False


class _FakeTokenResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "user": {"email": "user@example.com"},
        }


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.posted_url = ""
        self.posted_headers: dict = {}
        self.posted_json: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict):
        self.posted_url = url
        self.posted_headers = headers
        self.posted_json = json
        return _FakeTokenResponse()


@pytest.fixture(autouse=True)
def configured_desktop_auth(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "DESKTOP_AUTH_ALLOWED_CALLBACKS", CALLBACK_URL)
    monkeypatch.setattr(
        auth_router.settings,
        "DESKTOP_AUTH_PUBLIC_BASE_URL",
        "https://api.example.com",
    )
    monkeypatch.setattr(auth_router.settings, "SUPABASE_PUBLIC_URL", "https://auth.example.com")
    monkeypatch.setattr(auth_router.settings, "FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setattr(auth_router.settings, "APP_ENV", "test")
    monkeypatch.setenv("SUPABASE_URL", "https://auth.internal")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")


async def test_desktop_oauth_pkce_state_and_exchange_are_single_use(monkeypatch):
    store = _MemorySecurityStore()
    client = _FakeAsyncClient()
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda: client)

    started = auth_router.desktop_auth_start(
        auth_router.DesktopStartRequest(
            provider="github",
            callback_url=CALLBACK_URL,
            code_challenge=DESKTOP_CHALLENGE,
            code_challenge_method="S256",
        ),
        store=store,
    )
    state = started.data.state
    pending = store.values[("desktop-state", state)]
    authorize_query = parse_qs(urlsplit(started.data.login_url).query)

    assert authorize_query["provider"] == ["github"]
    assert authorize_query["code_challenge_method"] == ["s256"]
    assert authorize_query["code_challenge"][0]
    assert pending["callback_url"] == CALLBACK_URL
    assert pending["code_verifier"]

    redirected = await auth_router.desktop_auth_callback(
        code="provider-code",
        state=state,
        store=store,
    )
    callback_query = parse_qs(urlsplit(redirected.headers["location"]).query)
    exchange_code = callback_query["code"][0]

    assert callback_query["state"] == [state]
    assert client.posted_url == "https://auth.internal/auth/v1/token?grant_type=pkce"
    assert client.posted_json == {
        "auth_code": "provider-code",
        "code_verifier": pending["code_verifier"],
    }
    assert ("desktop-state", state) not in store.values

    exchanged = auth_router.desktop_auth_exchange(
        auth_router.DesktopExchangeRequest(
            code=exchange_code,
            state=state,
            code_verifier=DESKTOP_VERIFIER,
            redirect_uri=CALLBACK_URL,
        ),
        store=store,
    )
    assert exchanged.data["access_token"] == "access-token"

    with pytest.raises(HTTPException) as replay:
        auth_router.desktop_auth_exchange(
            auth_router.DesktopExchangeRequest(
                code=exchange_code,
                state=state,
                code_verifier=DESKTOP_VERIFIER,
                redirect_uri=CALLBACK_URL,
            ),
            store=store,
        )
    assert replay.value.status_code == 400


async def test_desktop_loopback_callback_is_bound_to_native_pkce(monkeypatch):
    store = _MemorySecurityStore()
    client = _FakeAsyncClient()
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda: client)

    started = auth_router.desktop_auth_start(
        auth_router.DesktopStartRequest(
            provider="google",
            callback_url=LOOPBACK_CALLBACK_URL,
            code_challenge=DESKTOP_CHALLENGE,
            code_challenge_method="S256",
        ),
        store=store,
    )
    state = started.data.state
    pending = store.values[("desktop-state", state)]
    assert pending["callback_url"] == LOOPBACK_CALLBACK_URL
    assert pending["desktop_code_challenge"] == DESKTOP_CHALLENGE

    redirected = await auth_router.desktop_auth_callback(
        code="provider-code",
        state=state,
        store=store,
    )
    callback_query = parse_qs(urlsplit(redirected.headers["location"]).query)
    exchange_code = callback_query["code"][0]

    exchanged = auth_router.desktop_auth_exchange(
        auth_router.DesktopExchangeRequest(
            code=exchange_code,
            state=state,
            code_verifier=DESKTOP_VERIFIER,
            redirect_uri=LOOPBACK_CALLBACK_URL,
        ),
        store=store,
    )
    assert exchanged.data["access_token"] == "access-token"


def test_desktop_generic_sign_in_uses_browser_login_page():
    store = _MemorySecurityStore()
    started = auth_router.desktop_auth_start(
        auth_router.DesktopStartRequest(
            callback_url=LOOPBACK_CALLBACK_URL,
            code_challenge=DESKTOP_CHALLENGE,
            code_challenge_method="S256",
        ),
        store=store,
    )

    login_url = urlsplit(started.data.login_url)
    login_query = parse_qs(login_url.query)
    assert login_url.scheme == "http"
    assert login_url.netloc == "localhost:3000"
    assert login_url.path == "/login"
    assert login_query == {
        "client": ["desktop"],
        "desktop_state": [started.data.state],
    }


def test_desktop_browser_completion_binds_verified_session_to_native_pkce():
    store = _MemorySecurityStore()
    started = auth_router.desktop_auth_start(
        auth_router.DesktopStartRequest(
            callback_url=LOOPBACK_CALLBACK_URL,
            code_challenge=DESKTOP_CHALLENGE,
            code_challenge_method="S256",
        ),
        store=store,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/desktop/complete",
            "headers": [(b"authorization", b"Bearer browser-access-token")],
        }
    )
    current_user = CurrentUser(
        user_id="user-1",
        email="user@example.com",
        role="authenticated",
    )
    completed = auth_router.desktop_auth_complete(
        auth_router.DesktopCompleteRequest(
            state=started.data.state,
            access_token="browser-access-token",
            refresh_token="browser-refresh-token",
            expires_in=3600,
            user_email="user@example.com",
        ),
        request=request,
        current_user=current_user,
        store=store,
    )
    callback_query = parse_qs(urlsplit(completed.data.redirect_url).query)

    exchanged = auth_router.desktop_auth_exchange(
        auth_router.DesktopExchangeRequest(
            code=callback_query["code"][0],
            state=started.data.state,
            code_verifier=DESKTOP_VERIFIER,
            redirect_uri=LOOPBACK_CALLBACK_URL,
        ),
        store=store,
    )
    assert callback_query["state"] == [started.data.state]
    assert exchanged.data["access_token"] == "browser-access-token"
    assert exchanged.data["refresh_token"] == "browser-refresh-token"
    assert exchanged.data["user_id"] == "user-1"
    assert exchanged.data["user_email"] == "user@example.com"


def test_desktop_browser_completion_rejects_mismatched_bearer_without_consuming_state():
    store = _MemorySecurityStore()
    started = auth_router.desktop_auth_start(
        auth_router.DesktopStartRequest(
            callback_url=LOOPBACK_CALLBACK_URL,
            code_challenge=DESKTOP_CHALLENGE,
            code_challenge_method="S256",
        ),
        store=store,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/desktop/complete",
            "headers": [(b"authorization", b"Bearer verified-access-token")],
        }
    )

    with pytest.raises(HTTPException) as invalid:
        auth_router.desktop_auth_complete(
            auth_router.DesktopCompleteRequest(
                state=started.data.state,
                access_token="different-access-token",
                refresh_token="refresh-token",
            ),
            request=request,
            current_user=CurrentUser(
                user_id="user-1",
                email="user@example.com",
                role="authenticated",
            ),
            store=store,
        )
    assert invalid.value.status_code == 401
    assert ("desktop-state", started.data.state) in store.values


def test_desktop_loopback_exchange_rejects_wrong_pkce_and_burns_code():
    store = _MemorySecurityStore()
    store.put(
        "desktop-exchange",
        "one-time-code",
        {
            "state": "expected",
            "session": {"access_token": "token"},
            "callback_url": LOOPBACK_CALLBACK_URL,
            "desktop_code_challenge": DESKTOP_CHALLENGE,
        },
        60,
    )

    with pytest.raises(HTTPException) as mismatch:
        auth_router.desktop_auth_exchange(
            auth_router.DesktopExchangeRequest(
                code="one-time-code",
                state="expected",
                code_verifier="wrong-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFG",
                redirect_uri=LOOPBACK_CALLBACK_URL,
            ),
            store=store,
        )
    assert mismatch.value.status_code == 400

    with pytest.raises(HTTPException) as consumed:
        auth_router.desktop_auth_exchange(
            auth_router.DesktopExchangeRequest(
                code="one-time-code",
                state="expected",
                code_verifier=DESKTOP_VERIFIER,
                redirect_uri=LOOPBACK_CALLBACK_URL,
            ),
            store=store,
        )
    assert consumed.value.status_code == 400


def test_desktop_exchange_state_mismatch_fails_closed_and_burns_code():
    store = _MemorySecurityStore()
    store.put(
        "desktop-exchange",
        "one-time-code",
        {"state": "expected", "session": {"access_token": "token"}},
        60,
    )

    with pytest.raises(HTTPException) as mismatch:
        auth_router.desktop_auth_exchange(
            auth_router.DesktopExchangeRequest(code="one-time-code", state="wrong"),
            store=store,
        )
    assert mismatch.value.status_code == 400

    with pytest.raises(HTTPException) as consumed:
        auth_router.desktop_auth_exchange(
            auth_router.DesktopExchangeRequest(code="one-time-code", state="expected"),
            store=store,
        )
    assert consumed.value.status_code == 400


@pytest.mark.parametrize(
    "callback_url",
    [
        "http://localhost:43123/auth/callback",
        "http://127.0.0.1/auth/callback",
        "http://127.0.0.1:43123/wrong",
        "http://127.0.0.1:43123/auth/callback?unexpected=1",
        "https://127.0.0.1:43123/auth/callback",
        "puppyone://auth/callback?unexpected=1",
    ],
)
def test_desktop_callback_requires_an_exact_allowlisted_redirect(callback_url):
    with pytest.raises(HTTPException) as invalid:
        auth_router._validate_desktop_callback(callback_url, allow_loopback=True)
    assert invalid.value.status_code == 400


def test_desktop_loopback_callback_requires_pkce():
    with pytest.raises(HTTPException) as missing_pkce:
        auth_router.desktop_auth_start(
            auth_router.DesktopStartRequest(
                provider="google",
                callback_url=LOOPBACK_CALLBACK_URL,
            ),
            store=_MemorySecurityStore(),
        )
    assert missing_pkce.value.status_code == 400


def test_desktop_allowlisted_callback_still_requires_pkce():
    with pytest.raises(HTTPException) as missing_pkce:
        auth_router.desktop_auth_start(
            auth_router.DesktopStartRequest(
                provider="github",
                callback_url=CALLBACK_URL,
            ),
            store=_MemorySecurityStore(),
        )
    assert missing_pkce.value.status_code == 400


@pytest.mark.parametrize(
    ("challenge", "method"),
    [
        ("short", "S256"),
        (DESKTOP_CHALLENGE, "plain"),
        (DESKTOP_CHALLENGE, None),
    ],
)
def test_desktop_loopback_callback_rejects_invalid_pkce(challenge, method):
    with pytest.raises(HTTPException) as invalid:
        auth_router.desktop_auth_start(
            auth_router.DesktopStartRequest(
                provider="google",
                callback_url=LOOPBACK_CALLBACK_URL,
                code_challenge=challenge,
                code_challenge_method=method,
            ),
            store=_MemorySecurityStore(),
        )
    assert invalid.value.status_code == 400


def test_desktop_start_rejects_unsupported_provider():
    with pytest.raises(HTTPException) as invalid:
        auth_router.desktop_auth_start(
            auth_router.DesktopStartRequest(provider="password", callback_url=CALLBACK_URL),
            store=_MemorySecurityStore(),
        )
    assert invalid.value.status_code == 400


def test_desktop_start_fails_closed_when_shared_store_is_unavailable():
    class _UnavailableStore(_MemorySecurityStore):
        def put(self, namespace: str, key: str, value: dict, ttl_seconds: int) -> None:
            raise SecurityStoreUnavailable("offline")

    with pytest.raises(HTTPException) as unavailable:
        auth_router.desktop_auth_start(
            auth_router.DesktopStartRequest(
                provider="google",
                callback_url=CALLBACK_URL,
                code_challenge=DESKTOP_CHALLENGE,
                code_challenge_method="S256",
            ),
            store=_UnavailableStore(),
        )
    assert unavailable.value.status_code == 503


def test_security_store_dependency_failure_is_reported_as_service_unavailable():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/desktop/start",
            "headers": [],
        }
    )
    response = security_store_unavailable_handler(
        request,
        SecurityStoreUnavailable("AUTH_SECURITY_REDIS_URL is not configured"),
    )

    assert response.status_code == 503
    assert json.loads(response.body)["message"] == (
        "Authentication security store unavailable"
    )
