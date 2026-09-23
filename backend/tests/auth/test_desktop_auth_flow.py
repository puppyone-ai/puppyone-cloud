"""Exercise the registered HTTP routes, not private router functions."""

from copy import deepcopy
from threading import Lock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.exception_handler import security_store_unavailable_handler
from src.platform.auth.desktop_router import get_desktop_service, get_session_verifier
from src.platform.auth.desktop_service import DesktopAuthService, challenge_for
from src.platform.auth.router import router
from src.platform.auth.shared_security_store import SecurityStoreUnavailable

CALLBACK = "http://127.0.0.1:43123/auth/callback"
VERIFIER = "native-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFG"
PROOF = "a" * 64


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.lock = Lock()
        self.limited = False

    def put(self, namespace, key, value, ttl_seconds):
        self.values[namespace, key] = deepcopy(value)

    def read(self, namespace, key):
        return deepcopy(self.values.get((namespace, key)))

    def transition(self, namespace, key, expected, *, replacement=None, destination=None):
        with self.lock:
            if self.values.get((namespace, key)) != expected:
                return False
            if replacement is None:
                del self.values[namespace, key]
            else:
                self.values[namespace, key] = deepcopy(replacement)
            if destination:
                ns, target, value, ttl = destination
                self.put(ns, target, value, ttl)
            return True

    def hit(self, *_args):
        return self.limited


class VerifiedSession:
    calls = 0

    async def verify_pair(self, access, refresh):
        self.calls += 1
        assert (access, refresh) == ("browser-access", "browser-refresh")
        return {
            "access_token": "native-access",
            "refresh_token": "native-refresh",
            "expires_in": 3600,
            "user_id": "user-1",
            "user_email": "user@example.test",
        }

    async def token_request(self, grant, payload):
        assert grant == "pkce"
        return {"access_token": "browser-access", "refresh_token": "browser-refresh"}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.example.test")
    monkeypatch.setattr(settings, "DESKTOP_AUTH_ALLOWED_CALLBACKS", "puppyone://auth/callback")
    store = MemoryStore()
    verifier = VerifiedSession()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(SecurityStoreUnavailable, security_store_unavailable_handler)
    app.dependency_overrides[get_desktop_service] = lambda: DesktopAuthService(store)
    app.dependency_overrides[get_session_verifier] = lambda: verifier
    with TestClient(app) as client:
        yield client, store, verifier


def post(api, path, body, **kwargs):
    return api[0].post("/api/v1/auth/desktop/" + path, json=body, **kwargs)


def start(api, **overrides):
    body = {
        "callback_url": CALLBACK,
        "code_challenge": challenge_for(VERIFIER),
        "code_challenge_method": "S256",
        **overrides,
    }
    return post(api, "start", body)


def bind(api, state, proof=PROOF):
    return post(api, "bind", {"state": state, "browser_proof": proof})


def complete(api, state, **overrides):
    return post(
        api,
        "complete",
        {
            "state": state,
            "browser_proof": PROOF,
            "access_token": "browser-access",
            "refresh_token": "browser-refresh",
            **overrides,
        },
        headers={"Authorization": "Bearer browser-access"},
    )


def exchange_body(redirect):
    params = parse_qs(urlsplit(redirect).query)
    return {
        "code": params["code"][0],
        "state": params["state"][0],
        "code_verifier": VERIFIER,
        "redirect_uri": CALLBACK,
    }


@pytest.mark.parametrize("provider", [None, "google", "github"])
def test_all_methods_enter_same_login_and_complete_once(api, provider):
    response = start(api, provider=provider)
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    result = response.json()["data"]
    assert urlsplit(result["login_url"]).path == "/login"
    assert parse_qs(urlsplit(result["login_url"]).query)["client"] == ["desktop"]
    state = result["state"]
    assert bind(api, state).status_code == 200
    assert bind(api, state).status_code == 200
    assert bind(api, state, "b" * 64).status_code == 400
    assert complete(api, state, browser_proof="b" * 64).status_code == 400
    assert api[2].calls == 0
    completed = complete(api, state)
    assert completed.status_code == 200
    callback = completed.json()["data"]["redirect_url"]
    assert "access" not in callback and "refresh" not in callback
    assert complete(api, state).status_code == 400
    body = exchange_body(callback)
    # A bad proof, state, or URI must not burn the legitimate one-time code.
    for changes in [
        {"code_verifier": "b" * 64},
        {"state": "wrong"},
        {"redirect_uri": CALLBACK.replace("43123", "43124")},
        {"code_verifier": None},
        {"redirect_uri": None},
    ]:
        assert post(api, "exchange", {**body, **changes}).status_code == 400
    response = post(api, "exchange", body)
    assert response.status_code == 200
    assert response.json()["data"]["refresh_token"] == "native-refresh"
    assert "no-store" in response.headers["cache-control"]
    assert post(api, "exchange", body).status_code == 400


def test_binding_and_bearer_required_without_consuming_attempt(api):
    state = start(api).json()["data"]["state"]
    assert complete(api, state).status_code == 400
    bind(api, state)
    assert complete(api, state, browser_proof=None).status_code == 400
    assert complete(api, state, access_token="different").status_code == 401
    assert api[2].calls == 0
    assert complete(api, state).status_code == 200


@pytest.mark.parametrize(
    "callback",
    [
        "http://localhost:43123/auth/callback",
        "http://127.0.0.1/auth/callback",
        "http://127.0.0.1:0/auth/callback",
        "http://127.0.0.1:43123/wrong",
        CALLBACK + "?x=1",
        CALLBACK + "#x",
        "https://evil.example/auth/callback",
        "puppyone://auth/callback?x=1",
        "http://user@127.0.0.1:43123/auth/callback",
    ],
)
def test_callback_requires_exact_loopback_or_allowlist(api, callback):
    assert start(api, callback_url=callback).status_code == 400


@pytest.mark.parametrize(
    "changes",
    [
        {"code_challenge": None},
        {"code_challenge": "short"},
        {"code_challenge_method": "plain"},
        {"code_challenge_method": None},
        {"provider": "unsupported"},
    ],
)
def test_start_rejects_invalid_pkce_and_providers(api, changes):
    assert start(api, **changes).status_code == 400


def test_legacy_inflight_email_and_oauth_can_drain(api):
    pending = {"callback_url": CALLBACK, "desktop_code_challenge": challenge_for(VERIFIER)}
    for oauth in [False, True]:
        state = ("o" if oauth else "e") * 43
        api[1].put(
            "desktop-state",
            state,
            {**pending, **({"code_verifier": "legacy-verifier"} if oauth else {})},
            600,
        )
        response = (
            api[0].get(
                f"/api/v1/auth/desktop/callback?state={state}&code=provider-code",
                follow_redirects=False,
            )
            if oauth
            else complete(api, state, browser_proof=None)
        )
        assert response.status_code == (302 if oauth else 200)
        callback = (
            response.headers["location"] if oauth else response.json()["data"]["redirect_url"]
        )
        assert post(api, "exchange", exchange_body(callback)).status_code == 200


def test_legacy_callback_cannot_bypass_new_browser_binding(api):
    state = start(api).json()["data"]["state"]
    response = api[0].get(f"/api/v1/auth/desktop/callback?state={state}&code=provider-code")
    assert response.status_code == 400
    assert api[1].read("desktop-state", state)


def test_rate_limit_and_store_failure_are_closed(api, monkeypatch):
    api[1].limited = True
    assert start(api).status_code == 429
    api[1].limited = False

    def unavailable(*_args):
        raise SecurityStoreUnavailable("offline")

    monkeypatch.setattr(api[1], "put", unavailable)
    response = start(api)
    assert response.status_code == 503
    assert "offline" not in response.text
