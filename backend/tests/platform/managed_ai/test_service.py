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
async def test_tool_stream_persists_identity_before_content_and_reports_metered_usage():
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
            + event(
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "cost": 0.000012,
                    "prompt_tokens_details": {"cached_tokens": 0},
                }
            )
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
    assert ledger.calls[-1][1]["body"]["input_tokens"] == 12
    assert b'"cost"' not in b"".join(chunks)
    assert response.headers["X-PuppyOne-Reservation-ID"]
    assert captured["user"] == provider_user("user-one")


@pytest.mark.asyncio
async def test_pi_tool_continuation_forwards_context_and_settles_its_own_usage():
    ledger = Ledger()
    captured = {}
    messages = [
        {"role": "user", "content": "Read the fixture."},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "Synthetic context.",
            "tool_calls": [
                {
                    "id": "call-read",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"fixture.txt"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-read", "content": "Synthetic tool result."},
    ]

    def provider(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text=event(choices=[{"delta": {"content": "Read complete."}}])
            + event(
                usage={
                    "prompt_tokens": 80,
                    "completion_tokens": 6,
                    "prompt_tokens_details": {"cached_tokens": 0},
                }
            )
            + "data: [DONE]\n\n",
        )

    service = ManagedAIService(ledger, key="key", transport=httpx.MockTransport(provider))
    response = await service.completion("user-one", "pi-tool-continuation", body(messages=messages))
    result = b"".join([chunk async for chunk in response.body_iterator])
    assert captured["messages"] == messages
    assert b"Read complete." in result
    assert b"Synthetic context." not in result
    assert result.endswith(b"data: [DONE]\n\n")
    assert [path.rsplit("/", 1)[-1] for path, _ in ledger.calls] == [
        "catalog",
        "reservations",
        "start",
        "provider",
        "settle",
    ]
    assert ledger.calls[-1][1]["body"]["input_tokens"] == 80
    assert ledger.calls[-1][1]["body"]["output_tokens"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("cost", [None, "not-a-cost", 0.99])
async def test_complete_tokens_settle_without_supplier_cost(cost):
    ledger = Ledger()
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 10},
    }
    if cost is not None:
        usage["cost"] = cost
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=event(usage=usage) + "data: [DONE]\n\n")
        ),
    )
    response = await service.completion("user", "cost-optional-request", body())
    result = b"".join([chunk async for chunk in response.body_iterator])
    assert result.endswith(b"data: [DONE]\n\n")
    payload = ledger.calls[-1][1]["body"]
    assert (payload["input_tokens"], payload["cached_tokens"], payload["output_tokens"]) == (
        100,
        30,
        20,
    )
    assert payload["reasoning_tokens"] == 10
    assert b'"cost"' not in result
    assert ("provider_cost_usd" in payload) is (cost == 0.99)


@pytest.mark.asyncio
async def test_stream_and_recovery_use_identical_native_tokens_without_cost():
    from src.platform.managed_ai.openrouter_usage import normalize_usage

    stream = normalize_usage(
        "gen-test",
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    )
    data = {
        "id": "gen-test",
        "external_user": provider_user("user"),
        "finish_reason": "stop",
        "native_tokens_prompt": 100,
        "native_tokens_completion": 20,
        "native_tokens_cached": 30,
        "native_tokens_reasoning": 10,
        "tokens_prompt": 999,
        "tokens_completion": 999,
    }
    service = ManagedAIService(
        Ledger(),
        key="key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data})),
    )
    recovered = await service.recover_usage("gen-test", "user")
    assert {k: v for k, v in stream.items() if k != "cost_source"} == {
        k: v for k, v in recovered.items() if k != "cost_source"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"prompt_tokens": 100, "completion_tokens": 20},
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 101},
        },
        {
            "prompt_tokens": True,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    ],
)
async def test_incomplete_or_invalid_usage_never_becomes_zero_cost_success(invalid):
    ledger = Ledger()
    service = ManagedAIService(
        ledger,
        key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=event(usage=invalid) + "data: [DONE]\n\n")
        ),
    )
    response = await service.completion("user", "invalid-meter-request", body())
    result = b"".join([chunk async for chunk in response.body_iterator])
    assert b"ai_usage_pending" in result and b"[DONE]" not in result
    assert not any(path.endswith("/settle") for path, _ in ledger.calls)


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
        "native_tokens_cached": 0,
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
