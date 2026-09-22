"""Cluster-safe endpoint regressions for ISSUE-004 and ISSUE-006."""

from __future__ import annotations

import base64
import hashlib
import time
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.platform.auth import router as auth_router
from src.platform.auth.shared_security_store import get_auth_security_store

DESKTOP_VERIFIER = "desktop-verifier-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFG"
DESKTOP_CHALLENGE = base64.urlsafe_b64encode(
    hashlib.sha256(DESKTOP_VERIFIER.encode("ascii")).digest()
).rstrip(b"=").decode("ascii")


class _SharedStore:
    def __init__(self):
        self.values = {}
        self.counts = {}
        self.lock = Lock()

    def put(self, namespace, key, value, ttl_seconds):
        with self.lock:
            self.values[(namespace, key)] = (deepcopy(value), time.monotonic() + ttl_seconds)

    def consume(self, namespace, key):
        with self.lock:
            record = self.values.pop((namespace, key), None)
        if not record or record[1] <= time.monotonic():
            return None
        return deepcopy(record[0])

    def hit(self, bucket, subject, limit, window_seconds):
        key = (bucket, subject)
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key] > limit


def _app(store):
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_auth_security_store] = lambda: store
    return app


class _FakeAsyncResponse:
    status_code = 200

    @staticmethod
    def json():
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "user": {"email": "user@example.com"},
        }


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return _FakeAsyncResponse()


def test_desktop_oauth_crosses_instances_and_rejects_replay(monkeypatch):
    store = _SharedStore()
    first = TestClient(_app(store))
    second = TestClient(_app(store))
    monkeypatch.setattr(settings, "DESKTOP_AUTH_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(settings, "DESKTOP_AUTH_ALLOWED_CALLBACKS", "puppyone://auth/callback")
    monkeypatch.setattr(settings, "SUPABASE_PUBLIC_URL", "https://login.example.com")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-internal:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", _FakeAsyncClient)

    started = first.post(
        "/auth/desktop/start",
        json={
            "provider": "google",
            "callback_url": "puppyone://auth/callback",
            "code_challenge": DESKTOP_CHALLENGE,
            "code_challenge_method": "S256",
        },
    ).json()["data"]
    state = started["state"]
    assert started["login_url"].startswith("https://login.example.com/auth/v1/authorize?")
    assert "code_challenge=" in started["login_url"]

    callback = second.get(
        "/auth/desktop/callback",
        params={"code": "provider-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    query = parse_qs(urlparse(callback.headers["location"]).query)
    exchange_code = query["code"][0]

    exchanged = first.post(
        "/auth/desktop/exchange",
        json={
            "code": exchange_code,
            "state": state,
            "code_verifier": DESKTOP_VERIFIER,
            "redirect_uri": "puppyone://auth/callback",
        },
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["data"]["access_token"] == "access"
    assert second.post(
        "/auth/desktop/exchange",
        json={
            "code": exchange_code,
            "state": state,
            "code_verifier": DESKTOP_VERIFIER,
            "redirect_uri": "puppyone://auth/callback",
        },
    ).status_code == 400
    assert first.get(
        "/auth/desktop/callback",
        params={"code": "provider-code", "state": state},
    ).status_code == 400

    expired = first.post(
        "/auth/desktop/start",
        json={
            "provider": "google",
            "callback_url": "puppyone://auth/callback",
            "code_challenge": DESKTOP_CHALLENGE,
            "code_challenge_method": "S256",
        },
    ).json()["data"]["state"]
    value, _deadline = store.values[("desktop-state", expired)]
    store.values[("desktop-state", expired)] = (value, 0)
    assert second.get(
        "/auth/desktop/callback",
        params={"code": "provider-code", "state": expired},
    ).status_code == 400


def test_login_global_limit_blocks_before_supabase(monkeypatch):
    store = _SharedStore()
    first = TestClient(_app(store))
    second = TestClient(_app(store))
    calls = []

    class _Auth:
        def sign_in_with_password(self, payload):
            calls.append(payload)
            return SimpleNamespace(
                session=SimpleNamespace(access_token="a", refresh_token="r", expires_in=3600),
                user=SimpleNamespace(email=payload["email"]),
            )

    monkeypatch.setattr(auth_router, "_make_auth_client", lambda: SimpleNamespace(auth=_Auth()))
    body = {"email": "User@example.com", "password": "guess"}
    for index in range(auth_router._LOGIN_MAX_HITS):
        client = first if index % 2 else second
        assert client.post("/auth/login", json=body).status_code == 200
    blocked = second.post("/auth/login", json=body)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == str(auth_router._LOGIN_WINDOW)
    assert len(calls) == auth_router._LOGIN_MAX_HITS


def test_check_email_global_limit_has_retry_after():
    store = _SharedStore()
    client = TestClient(_app(store))
    subject = auth_router._hash_key("testclient")
    store.counts[("check-email", subject)] = auth_router._CHECK_EMAIL_MAX_HITS
    response = client.post("/auth/check-email", json={"email": "user@example.com"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == str(auth_router._CHECK_EMAIL_WINDOW)
