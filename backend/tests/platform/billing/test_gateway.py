from __future__ import annotations

import httpx
import pytest

from src.platform.billing.gateway import BillingGatewayError, PuppyPayGateway


@pytest.mark.asyncio
async def test_gateway_forwards_only_expected_credentials_and_idempotency() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    result = await PuppyPayGateway(
        base_url="https://pay.example.test",
        internal_secret="s" * 32,
        transport=httpx.MockTransport(handler),
    ).request(
        "POST",
        "/api/v1/billing/organizations/org-1/checkout",
        actor_user_id="user-1",
        actor_email="owner@example.com",
        idempotency_key="checkout-1",
        body={"plan_id": "plus", "seat_quantity": 1},
    )

    assert result == {"ok": True}
    assert "authorization" not in captured["headers"]
    assert captured["headers"]["x-internal-secret"] == "s" * 32
    assert captured["headers"]["x-puppyone-user-id"] == "user-1"
    assert captured["headers"]["x-puppyone-user-email"] == "owner@example.com"
    assert captured["headers"]["idempotency-key"] == "checkout-1"


@pytest.mark.asyncio
async def test_gateway_retries_safe_get_but_not_unkeyed_post() -> None:
    get_calls = 0

    def get_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            return httpx.Response(503, json={"error": {"code": "temporary"}})
        return httpx.Response(200, json={"plan_id": "free"})

    result = await PuppyPayGateway(
        base_url="https://pay.example.test",
        transport=httpx.MockTransport(get_handler),
    ).request("GET", "/api/v1/billing/organizations/org-1/summary")
    assert result["plan_id"] == "free"
    assert get_calls == 2

    post_calls = 0

    def post_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        post_calls += 1
        return httpx.Response(503, json={"error": {"code": "temporary"}})

    with pytest.raises(BillingGatewayError):
        await PuppyPayGateway(
            base_url="https://pay.example.test",
            transport=httpx.MockTransport(post_handler),
        ).request(
            "POST",
            "/api/v1/billing/organizations/org-1/seats/quote",
            body={"seat_quantity": 2},
        )
    assert post_calls == 1


@pytest.mark.asyncio
async def test_gateway_allows_authoritative_quote_inspection() -> None:
    captured_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_path
        captured_path = request.url.path
        return httpx.Response(200, json={"quote_id": "quote-1"})

    result = await PuppyPayGateway(
        base_url="https://pay.example.test",
        transport=httpx.MockTransport(handler),
    ).request(
        "GET",
        "/api/v1/billing/organizations/org-1/quotes/quote-1",
        actor_user_id="user-1",
    )

    assert result == {"quote_id": "quote-1"}
    assert captured_path == "/api/v1/billing/organizations/org-1/quotes/quote-1"


@pytest.mark.asyncio
async def test_gateway_rejects_paths_outside_fixed_billing_surface() -> None:
    gateway = PuppyPayGateway(base_url="https://pay.example.test")
    for path in (
        "/api/v1/admin",
        "/api/v1/billing/../admin",
        "/api/v1/billing/organizations/org-1/not-real",
        "https://attacker.test/api/v1/billing/catalog",
    ):
        with pytest.raises(ValueError):
            await gateway.request("GET", path)
