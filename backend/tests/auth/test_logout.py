from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from src.platform.auth import router as auth_router


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return dict(self._payload)


class _SessionProvider:
    def __init__(self) -> None:
        self.refresh_sessions = {"refresh-old": "session-1"}
        self.access_sessions: dict[str, str] = {}
        self.calls: list[tuple[str, dict, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict | None = None):
        self.calls.append((url, headers, json))
        if "grant_type=refresh_token" in url:
            refresh_token = str((json or {}).get("refresh_token", ""))
            session_id = self.refresh_sessions.get(refresh_token)
            if session_id is None:
                return _Response(400, {"error_code": "refresh_token_not_found"})
            access_token = f"access-{session_id}"
            # Model Supabase's refresh-token reuse window: both the submitted
            # token and its child remain usable until the session is revoked.
            self.refresh_sessions["refresh-child"] = session_id
            self.access_sessions[access_token] = session_id
            return _Response(
                200,
                {
                    "access_token": access_token,
                    "refresh_token": "refresh-child",
                    "expires_in": 3600,
                },
            )

        assert url.endswith("/auth/v1/logout?scope=local")
        access_token = headers.get("Authorization", "").removeprefix("Bearer ")
        session_id = self.access_sessions.pop(access_token, None)
        if session_id is None:
            return _Response(401)
        self.refresh_sessions = {
            token: owner
            for token, owner in self.refresh_sessions.items()
            if owner != session_id
        }
        return _Response(204)

    def sdk_refresh(self, refresh_token: str):
        if refresh_token not in self.refresh_sessions:
            raise RuntimeError("refresh token not found")
        return SimpleNamespace(
            session=SimpleNamespace(
                access_token="unexpected",
                refresh_token="unexpected",
                expires_in=3600,
            ),
            user=SimpleNamespace(email="user@example.com"),
        )


@pytest.mark.asyncio
async def test_logout_revokes_session_and_old_refresh_token_cannot_replay(monkeypatch) -> None:
    provider = _SessionProvider()
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda: provider)

    result = await auth_router.logout(
        auth_router.LogoutRequest(refresh_token="refresh-old")
    )

    assert result.data.revoked is True
    assert [call[0] for call in provider.calls] == [
        "https://auth.example.test/auth/v1/token?grant_type=refresh_token",
        "https://auth.example.test/auth/v1/logout?scope=local",
    ]
    assert provider.calls[1][1]["Authorization"] == "Bearer access-session-1"
    assert "refresh-old" not in provider.calls[1][1]["Authorization"]

    monkeypatch.setattr(
        auth_router,
        "_make_auth_client",
        lambda: SimpleNamespace(
            auth=SimpleNamespace(refresh_session=provider.sdk_refresh)
        ),
    )
    with pytest.raises(HTTPException) as replay:
        auth_router.refresh_token(auth_router.RefreshRequest(refresh_token="refresh-old"))
    assert replay.value.status_code == 401
    with pytest.raises(HTTPException):
        auth_router.refresh_token(auth_router.RefreshRequest(refresh_token="refresh-child"))


@pytest.mark.asyncio
async def test_logout_is_idempotent_for_an_already_invalid_refresh_token(monkeypatch) -> None:
    provider = _SessionProvider()
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda: provider)

    result = await auth_router.logout(
        auth_router.LogoutRequest(refresh_token="already-invalid")
    )

    assert result.data.revoked is True
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_logout_surfaces_provider_outage_without_echoing_refresh_token(monkeypatch) -> None:
    refresh_token = "refresh-do-not-echo"

    class OfflineProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", OfflineProvider)

    with pytest.raises(HTTPException) as unavailable:
        await auth_router.logout(auth_router.LogoutRequest(refresh_token=refresh_token))

    assert unavailable.value.status_code == 502
    assert refresh_token not in str(unavailable.value.detail)
