import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import settings
from src.exception_handler import http_exception_handler
from src.platform.auth.dependencies import get_current_user
from src.platform.billing.gateway import PuppyPayGateway, get_billing_gateway
from src.platform.managed_ai.router import internal_router, router
from src.platform.managed_ai.service import ManagedAIService
from src.utils.middleware import RequestContextMiddleware


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    # Personal AI credit must work while all hosted runtime/org billing is off.
    monkeypatch.setattr(settings, "BILLING_UI_ENABLED", False)
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", False)
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "disabled")
    instance = FastAPI()
    instance.add_exception_handler(StarletteHTTPException, http_exception_handler)
    instance.add_middleware(RequestContextMiddleware)
    instance.include_router(router, prefix="/api/v1")
    instance.include_router(internal_router)
    return instance


def user():
    return SimpleNamespace(
        user_id="user-one", email="test@example.com", role="authenticated", is_anonymous=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/v1/ai/trial", {}),
        ("GET", "/api/v1/ai/usage/11111111-1111-4111-8111-111111111111", None),
    ],
)
async def test_trial_and_receipt_cross_real_gateway_allowlist(app, method, path, body):
    calls = []

    def payment(request):
        calls.append(request)
        return httpx.Response(200, json={"accepted": True})

    gateway = PuppyPayGateway(
        base_url="https://pay.example.test",
        internal_secret="s" * 32,
        transport=httpx.MockTransport(payment),
    )
    app.dependency_overrides[get_billing_gateway] = lambda: gateway
    app.dependency_overrides[get_current_user] = user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            method, path, json=body, headers={"X-PuppyOne-User-ID": "victim"}
        )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].url.path == path
    assert calls[0].headers["x-puppyone-user-id"] == "user-one"
    assert calls[0].headers["x-internal-secret"] == "s" * 32


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
        payload = response.json()["error"]
        assert payload["code"] == "ai_request_invalid"
        assert payload["request_id"] == response.headers["x-request-id"]
        assert payload["request_id"] in payload["message"]
        assert (
            await client.post("/api/v1/ai/chat/completions", headers=headers, content=b"x" * 524289)
        ).status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_dependency_errors_use_model_protocol_without_private_details(app, status):
    def rejected():
        raise HTTPException(status, "private auth diagnostics", headers={"Retry-After": "3"})

    app.dependency_overrides[get_current_user] = rejected
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ai/chat/completions", json={}, headers={"X-Request-Id": "contract-request-123"}
        )
    assert response.status_code == status
    assert response.json()["error"]["message"]
    assert response.json()["error"]["request_id"] == "contract-request-123"
    assert response.headers["retry-after"] == "3"
    assert "private auth" not in response.text


@pytest.mark.asyncio
async def test_validation_diagnostics_log_only_schema_paths_and_categories(app, monkeypatch):
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_billing_gateway] = lambda: object()
    invoked = []
    monkeypatch.setattr(ManagedAIService, "completion", lambda *args: invoked.append(args))
    records = []
    sink = logger.add(
        lambda message: records.append(message.record),
        filter=lambda record: record["message"] == "managed_ai_request_rejected",
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/ai/chat/completions",
                json={
                    "model": "test/model",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "private-prompt",
                            "reasoning_content": {"secret-value": "private-context"},
                            "private-field-name": "private-value",
                        }
                    ],
                },
                headers={"Idempotency-Key": "invalid-protocol-request"},
            )
        assert response.status_code == 422
        assert not invoked
        issues = records[0]["extra"]["validation_issues"]
        assert {"type": "string_type", "path": ["messages", 0, "reasoning_content"]} in issues
        assert {"type": "extra_forbidden", "path": ["messages", 0, "<field>"]} in issues
        assert "private" not in json.dumps(issues) + response.text
    finally:
        logger.remove(sink)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tool", [False, True])
async def test_pi_continuation_accepts_assistant_reasoning_context(app, monkeypatch, with_tool):
    """Shape captured from the pinned Pi SDK after a reasoning response."""
    captured = []

    async def completion(_self, user_id, request_id, body):
        captured.append((user_id, body.model_dump(exclude_none=True)))
        return StreamingResponse(iter([b"data: [DONE]\n\n"]), media_type="text/event-stream")

    monkeypatch.setattr(ManagedAIService, "completion", completion)
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_billing_gateway] = lambda: object()
    assistant = {"role": "assistant", "content": "", "reasoning_content": "Synthetic context."}
    if with_tool:
        assistant["tool_calls"] = [
            {
                "id": "call-read",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"fixture.txt"}'},
            }
        ]
    messages = [{"role": "user", "content": "Read the fixture."}, assistant]
    messages.append(
        {"role": "tool", "tool_call_id": "call-read", "content": "Synthetic tool result."}
        if with_tool
        else {"role": "user", "content": "Continue."}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ai/chat/completions",
            headers={"Idempotency-Key": "pi-continuation-0001"},
            json={
                "model": "test/model",
                "messages": messages,
                "stream": True,
                "max_completion_tokens": 4096,
            },
        )
    assert response.status_code == 200
    assert captured[0][0] == "user-one"
    assert captured[0][1]["messages"] == messages
    assert "Synthetic context" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"reasoning_content": {}},
        {"reasoning_content": "private", "role": "user"},
        {"provider_override": "untrusted"},
    ],
)
async def test_reasoning_compatibility_keeps_request_validation_strict(app, change):
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_billing_gateway] = lambda: object()
    message = {"role": "assistant", "content": "private prompt", **change}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ai/chat/completions",
            headers={"Idempotency-Key": "pi-invalid-request-0001"},
            json={"model": "test/model", "messages": [message]},
        )
    assert response.status_code == 422
    assert "private" not in response.text


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
