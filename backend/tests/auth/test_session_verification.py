import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest

from src.config import settings
from src.exceptions import AuthException
from src.platform.auth.desktop_service import DesktopAuthError
from src.platform.auth.service import AuthService
from src.platform.auth.supabase_session import SupabaseSessionVerifier


def identity(**changes):
    return SimpleNamespace(
        user_id="user-1",
        session_id="session-1",
        role="authenticated",
        is_anonymous=False,
        email="one@example.test",
        **changes,
    )


async def test_handoff_requires_same_user_and_same_session():
    for field in ["user_id", "session_id", "role", "is_anonymous"]:
        original = identity()
        refreshed = identity()
        setattr(refreshed, field, "different" if field != "is_anonymous" else True)
        verifier = SupabaseSessionVerifier(
            Mock(verify_token=Mock(side_effect=[original, refreshed]))
        )
        verifier.token_request = AsyncMock(
            return_value={
                "access_token": "new",
                "refresh_token": "new-refresh",
                "user": {"id": "user-1"},
            }
        )
        with pytest.raises(DesktopAuthError) as error:
            await verifier.verify_pair("old", "refresh")
        assert error.value.status == 401


async def test_handoff_returns_rotated_pair():
    verifier = SupabaseSessionVerifier(Mock(verify_token=Mock(return_value=identity())))
    verifier.token_request = AsyncMock(
        return_value={
            "access_token": "rotated",
            "refresh_token": "rotated-refresh",
            "user": {"id": "user-1"},
        }
    )
    result = await verifier.verify_pair("old", "refresh")
    assert result["access_token"] == "rotated"
    assert result["user_id"] == "user-1"
    verifier.token_request.assert_awaited_once_with("refresh_token", {"refresh_token": "refresh"})


@pytest.mark.parametrize("status", [200, 400, 401, 500])
async def test_provider_adapter_sanitizes_responses(monkeypatch, status):
    real_client = httpx.AsyncClient

    def response(request):
        assert request.url.params["grant_type"] == "refresh_token"
        return httpx.Response(
            status,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "provider_token": "private-provider-token",
                "error": "private-provider-error",
            },
        )

    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-key")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(response), **kwargs),
    )
    verifier = SupabaseSessionVerifier(None)
    if status == 200:
        result = await verifier.token_request("refresh_token", {"refresh_token": "refresh"})
        assert result == {"access_token": "access", "refresh_token": "refresh"}
    else:
        with pytest.raises(DesktopAuthError) as error:
            await verifier.token_request("refresh_token", {"refresh_token": "refresh"})
        assert "private" not in str(error.value)
        assert error.value.status == (401 if status in [400, 401] else 502)


def test_valid_signature_does_not_allow_wrong_project_issuer(monkeypatch):
    secret = "test-secret-longer-than-thirty-two-bytes"
    monkeypatch.setattr(settings, "JWT_SECRET", secret)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "SUPABASE_PUBLIC_URL", "https://project.example.test")
    claims = {
        "sub": "user",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "iss": "https://project.example.test/auth/v1",
    }
    service = AuthService(Mock())
    assert service.verify_token(jwt.encode(claims, secret, algorithm="HS256")).user_id == "user"
    claims["iss"] = "https://other.example.test/auth/v1"
    with pytest.raises(AuthException, match="issuer"):
        service.verify_token(jwt.encode(claims, secret, algorithm="HS256"))
