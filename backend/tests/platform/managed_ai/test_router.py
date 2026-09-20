from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.billing.gateway import get_billing_gateway
from src.platform.managed_ai.router import internal_router, router


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    # Personal AI credit must work while all hosted runtime/org billing is off.
    monkeypatch.setattr(settings, "BILLING_UI_ENABLED", False)
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", False)
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "disabled")
    instance = FastAPI()
    instance.include_router(router, prefix="/api/v1")
    instance.include_router(internal_router)
    return instance


def user():
    return SimpleNamespace(
        user_id="user-one", email="test@example.com", role="authenticated", is_anonymous=False
    )


@pytest.mark.asyncio
async def test_personal_bff_uses_verified_user_not_body_or_actor_headers(app):
    calls = []

    class Gateway:
        async def request(self, method, path, **kwargs):
            calls.append(kwargs)
            return {"balance_micro_usd": 123}

    app.dependency_overrides[get_billing_gateway] = lambda: Gateway()
    app.dependency_overrides[get_current_user] = user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/ai/balance", headers={"X-PuppyOne-User-ID": "victim"})
        assert response.status_code == 200
        assert calls[-1]["actor_user_id"] == "user-one"
        response = await client.post(
            "/api/v1/ai/checkouts", json={"pack_id": "starter", "user_id": "victim"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_anonymous_and_machine_or_skip_auth_cannot_spend(app, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        def unsigned():
            raise HTTPException(401, "Sign in")

        app.dependency_overrides[get_current_user] = unsigned
        assert (await client.get("/api/v1/ai/balance")).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id="machine", role="service_role", is_anonymous=False
        )
        assert (await client.get("/api/v1/ai/balance")).status_code == 401
        app.dependency_overrides[get_current_user] = user
        monkeypatch.setattr(settings, "SKIP_AUTH", True)
        assert (await client.get("/api/v1/ai/balance")).status_code == 401


@pytest.mark.asyncio
async def test_feature_disabled_and_internal_recovery_unauthorized(app, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.get("/internal/ai/generations/gen-1?user_id=user-one")
        ).status_code == 403
        monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", False)
        assert (await client.get("/api/v1/ai/catalog")).status_code == 404


@pytest.mark.asyncio
async def test_validation_does_not_echo_prompt_and_rejects_oversized_body(app):
    app.dependency_overrides[get_current_user] = user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Idempotency-Key": "request-test-00001"}
        response = await client.post(
            "/api/v1/ai/chat/completions",
            headers=headers,
            json={
                "model": "test/model",
                "messages": [{"role": "invalid", "content": "private customer content"}],
            },
        )
        assert response.status_code == 422 and "private customer" not in response.text
        assert (
            await client.post("/api/v1/ai/chat/completions", headers=headers, content=b"x" * 524289)
        ).status_code == 413


@pytest.mark.asyncio
async def test_trial_uses_authenticated_identity_while_hosting_is_disabled(app):
    calls = []

    class Gateway:
        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"available_micro_usd": 1_000_000}

    app.dependency_overrides[get_billing_gateway] = lambda: Gateway()
    app.dependency_overrides[get_current_user] = user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ai/trial", json={}, headers={"X-PuppyOne-User-ID": "victim"}
        )
        assert response.status_code == 200
        assert calls == [
            (
                "POST",
                "/api/v1/ai/trial",
                {"actor_user_id": "user-one", "actor_email": "test@example.com", "body": {}},
            )
        ]
        assert (
            await client.post("/api/v1/ai/trial", json={"user_id": "victim", "amount": 100})
        ).status_code == 422
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id="anonymous", email="fake@example.com", role="authenticated", is_anonymous=True
        )
        assert (await client.post("/api/v1/ai/trial", json={})).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id="phone", email=None, role="authenticated", is_anonymous=False
        )
        assert (await client.post("/api/v1/ai/trial", json={})).status_code == 403
        assert len(calls) == 1
