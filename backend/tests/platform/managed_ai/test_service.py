import json
from uuid import uuid4

import httpx
import pytest

from src.platform.billing.gateway import BillingGatewayError
from src.platform.managed_ai.schemas import CompletionRequest
from src.platform.managed_ai.service import ManagedAIService, provider_user


class Ledger:
    def __init__(self):
        self.calls = []
        self.started = False
        self.insufficient = False

    async def request(self, method, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.endswith("catalog"):
            return {
                "models": [{"id": "test/model", "context_window": 32768, "max_output_tokens": 4096}]
            }
        if path.endswith("reservations"):
            if self.insufficient:
                raise BillingGatewayError(402, {"error": {"code": "ai_balance_insufficient"}})
            return {"reservation_id": str(uuid4())}
        if path.endswith("start"):
            assert kwargs["idempotency_key"] is None
            if self.started:
                raise BillingGatewayError(409, {"error": {"code": "ai_request_already_started"}})
            self.started = True
        return {}


def body(**changes):
    return CompletionRequest.model_validate(
        {
            "model": "test/model",
            "messages": [{"role": "user", "content": "Call the local test tool."}],
            **changes,
        }
    )


def event(**values):
    return f"data: {json.dumps({'id': 'gen-test', **values})}\n\n"


@pytest.mark.asyncio
async def test_tool_stream_persists_identity_before_content_and_settles_actual_cost():
    ledger = Ledger()
    captured = {}

    def provider(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text=event(
                choices=[
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            )
            + event(usage={"prompt_tokens": 12, "completion_tokens": 8, "cost": 0.000012})
            + "data: [DONE]\n\n",
        )

    service = ManagedAIService(
        ledger, key="server-only-key", transport=httpx.MockTransport(provider)
    )
    response = await service.completion("user-one", "unique-request-0001", body())
    chunks = []
    async for chunk in response.body_iterator:
        if not chunks:
            assert ledger.calls[-1][0].endswith("/provider")
        chunks.append(chunk)
    assert b"tool_calls" in b"".join(chunks)
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert ledger.calls[-1][0].endswith("/settle")
    assert ledger.calls[-1][1]["body"]["provider_cost_usd"] == "0.000012"
    assert captured["user"] == provider_user("user-one")


@pytest.mark.asyncio
async def test_insufficient_funds_and_replayed_start_never_invoke_provider():
    ledger = Ledger()

    def forbidden(_request):
        pytest.fail("Provider must not be invoked")

    service = ManagedAIService(ledger, key="key", transport=httpx.MockTransport(forbidden))
    ledger.insufficient = True
    with pytest.raises(BillingGatewayError) as failure:
        await service.completion("user", "unique-request-0001", body())
    assert failure.value.status_code == 402
    ledger.insufficient = False
    ledger.started = True
    with pytest.raises(BillingGatewayError) as failure:
        await service.completion("user", "unique-request-0001", body())
    assert failure.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("status,released", [(429, True), (400, True), (500, False)])
async def test_only_confirmed_provider_rejections_release_reservation(status, released):
    ledger = Ledger()
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"error": "private provider detail"})
        ),
    )
    with pytest.raises(BillingGatewayError) as failure:
        await service.completion("user", "unique-request-0001", body())
    assert "private" not in str(failure.value.payload)
    assert any(path.endswith("release") for path, _ in ledger.calls) is released


@pytest.mark.asyncio
async def test_missing_usage_holds_credit_and_does_not_report_success():
    ledger = Ledger()
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=event(choices=[]) + "data: [DONE]\n\n")
        ),
    )
    response = await service.completion("user", "unique-request-0001", body())
    result = b"".join([chunk async for chunk in response.body_iterator])
    assert b"ai_usage_pending" in result and b"[DONE]" not in result
    assert not any(path.endswith(("release", "settle")) for path, _ in ledger.calls)


@pytest.mark.asyncio
async def test_disconnect_retains_durable_generation_for_recovery():
    ledger = Ledger()
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=event(choices=[]) + "data: [DONE]\n\n")
        ),
    )
    response = await service.completion("user", "unique-request-0001", body())
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert ledger.calls[-1][0].endswith("provider")


@pytest.mark.asyncio
async def test_recovery_requires_same_user_and_final_provider_usage():
    ledger = Ledger()
    result = {
        "id": "gen-test",
        "external_user": provider_user("user"),
        "finish_reason": "stop",
        "total_cost": 0.000021,
        "native_tokens_prompt": 21,
        "native_tokens_completion": 10,
    }
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": result})),
    )
    assert (await service.recover_usage("gen-test", "user"))["provider_cost_usd"] == "0.000021"
    with pytest.raises(BillingGatewayError):
        await service.recover_usage("gen-test", "another-user")


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": {"base_url": "https://attacker.test"}},
        {"user_id": "victim"},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": "https://private"}]}
            ]
        },
        {"tools": [{"type": "web_search"}]},
        {"max_tokens": 0},
    ],
)
def test_client_cannot_supply_provider_identity_remote_media_or_hosted_tools(changes):
    with pytest.raises(ValueError):
        body(**changes)
