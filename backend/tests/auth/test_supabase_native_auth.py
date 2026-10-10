"""Native identity entry points work without any authentication Redis store."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.platform.auth import router as auth

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(redis.Redis, "from_url", Mock(side_effect=AssertionError("Authentication must not access Redis")))
    monkeypatch.setenv("AUTH_SECURITY_REDIS_URL", "redis://unreachable.invalid:1")
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test-key")
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client
    redis.Redis.from_url.assert_not_called()

@pytest.mark.parametrize("path", ["desktop/start", "desktop/bind", "desktop/complete", "desktop/exchange", "check-email"])
def test_server_owned_handoff_and_email_enumeration_are_absent(client, path):
    assert client.post("/api/v1/auth/" + path, json={}).status_code == 404

def test_public_config_requires_no_state_store(client):
    response = client.get("/api/v1/auth/config")
    assert response.status_code == 200
    assert response.json()["data"] == {"supabase_url": "https://auth.example.test", "supabase_anon_key": "public-test-key"}

@pytest.mark.parametrize("operation,body,method", [
    ("login", {"email": "person@example.test", "password": "example-password"}, "sign_in_with_password"),
    ("refresh", {"refresh_token": "example-refresh"}, "refresh_session"),
])
def test_cli_adapter_delegates_once_to_supabase_without_redis(client, monkeypatch, operation, body, method):
    upstream = Mock()
    getattr(upstream.auth, method).return_value = SimpleNamespace(
        session=SimpleNamespace(access_token="test-access", refresh_token="test-refresh", expires_in=3600),
        user=SimpleNamespace(email="person@example.test"),
    )
    monkeypatch.setattr(auth, "_make_auth_client", lambda: upstream)
    response = client.post("/api/v1/auth/" + operation, json=body)
    assert response.status_code == 200
    assert response.json()["data"]["access_token"] == "test-access"
    getattr(upstream.auth, method).assert_called_once()

@pytest.mark.parametrize("operation,body,method", [
    ("login", {"email": "person@example.test", "password": "example-password"}, "sign_in_with_password"),
    ("refresh", {"refresh_token": "example-refresh"}, "refresh_session"),
])
def test_native_supabase_rate_limit_is_preserved(client, monkeypatch, operation, body, method):
    upstream = Mock()
    error = Exception("upstream rate limit")
    error.status = 429
    getattr(upstream.auth, method).side_effect = error
    monkeypatch.setattr(auth, "_make_auth_client", lambda: upstream)
    response = client.post("/api/v1/auth/" + operation, json=body)
    assert response.status_code == 429
    getattr(upstream.auth, method).assert_called_once()
