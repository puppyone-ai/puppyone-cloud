"""Recovering token usage after a dropped stream, and the errors an Agent SDK sees.

When a stream ends before the provider's final usage arrives, the hold on the
customer's credit stays until recovery reads the generation from OpenRouter.
Recovery charges only final, complete usage of the same person's generation;
anything else stays pending so it is retried rather than guessed. The
completions endpoint answers every failure in the SDK's error shape, without
internals.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from src.platform.managed_ai.contracts import InferenceError
from src.platform.managed_ai.protocol import InferenceRoute
from src.platform.managed_ai.providers.openrouter import OpenRouterProvider, provider_user

FINAL = {
    "id": "gen-1",
    "external_user": provider_user("person"),
    "finish_reason": "stop",
    "native_tokens_prompt": 100,
    "native_tokens_completion": 20,
    "native_tokens_cached": 0,
    "total_cost": 0.0001,
}


def provider(handler) -> OpenRouterProvider:
    return OpenRouterProvider(key="key", transport=httpx.MockTransport(handler))


def answering(data: dict, status: int = 200):
    return provider(lambda _: httpx.Response(status, json={"data": data}))


@pytest.mark.asyncio
async def test_final_usage_of_the_same_persons_generation_is_recovered() -> None:
    usage = await answering(FINAL).recover("gen-1", "person")
    assert (usage.input_tokens, usage.output_tokens, usage.cost_source) == (100, 20, "generation")


@pytest.mark.asyncio
async def test_a_malformed_generation_id_never_reaches_the_provider() -> None:
    calls = []
    with pytest.raises(InferenceError) as caught:
        await provider(lambda request: calls.append(request)).recover("gen/../1", "person")
    assert (caught.value.status_code, caught.value.code) == (400, "ai_generation_invalid")
    assert calls == []


@pytest.mark.asyncio
async def test_another_persons_generation_is_never_charged_here() -> None:
    with pytest.raises(InferenceError) as caught:
        await answering(FINAL).recover("gen-1", "someone-else")
    assert (caught.value.status_code, caught.value.code) == (409, "ai_generation_mismatch")
    with pytest.raises(InferenceError) as caught:
        await answering({**FINAL, "id": "gen-2"}).recover("gen-1", "person")
    assert caught.value.code == "ai_generation_mismatch"


def unreachable(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("provider unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovering",
    [
        pytest.param(lambda: provider(unreachable), id="provider unreachable"),
        pytest.param(lambda: answering(FINAL, status=404), id="generation not indexed yet"),
        pytest.param(lambda: answering({**FINAL, "finish_reason": None}), id="still running"),
        pytest.param(
            lambda: answering({**FINAL, "native_tokens_completion": None}),
            id="token counts incomplete",
        ),
    ],
)
async def test_usage_that_is_not_final_and_complete_stays_pending(recovering) -> None:
    with pytest.raises(InferenceError) as caught:
        await recovering().recover("gen-1", "person")
    assert (caught.value.status_code, caught.value.code) == (503, "ai_usage_pending")


@pytest.mark.asyncio
async def test_a_cancelled_generation_is_final() -> None:
    usage = await answering({**FINAL, "finish_reason": None, "cancelled": True}).recover(
        "gen-1", "person"
    )
    assert usage.output_tokens == 20


class Body(BaseModel):
    model: str


def completions_app() -> FastAPI:
    router = APIRouter(route_class=InferenceRoute)

    @router.post("/invalid")
    async def invalid(body: Body) -> dict:
        return {}

    @router.post("/transport")
    async def transport() -> dict:
        raise httpx.ConnectError("upstream at 10.0.0.5 refused")

    @router.post("/crash")
    async def crash() -> dict:
        raise RuntimeError("secret prompt text")

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status", "kind", "code"),
    [
        ("/invalid", 422, "invalid_request_error", "ai_request_invalid"),
        ("/transport", 503, "server_error", "ai_unavailable"),
        ("/crash", 500, "server_error", "ai_unavailable"),
    ],
)
async def test_failures_reach_the_sdk_in_its_error_shape_without_internals(
    path, status, kind, code
) -> None:
    transport = httpx.ASGITransport(app=completions_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(path, json={"unexpected": "customer prompt"})

    assert response.status_code == status
    error = response.json()["error"]
    assert (error["type"], error["code"]) == (kind, code)
    assert error["request_id"] in error["message"]
    assert response.headers["X-Request-Id"] == error["request_id"]
    assert response.headers["Cache-Control"] == "no-store"
    for secret in ("customer prompt", "10.0.0.5", "secret prompt text"):
        assert secret not in response.text
