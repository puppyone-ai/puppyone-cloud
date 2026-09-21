"""Exercise vendor replacement through the same admission and settlement path."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from src.platform.managed_ai.contracts import InferenceError, MeteredUsage, ProviderEvent
from src.platform.managed_ai.providers.registry import ProviderRegistry
from src.platform.managed_ai.response import completion_response
from src.platform.managed_ai.service import ManagedAIService
from tests.platform.managed_ai.test_service import Ledger, body


class RoutedLedger(Ledger):
    def __init__(self, route):
        super().__init__()
        self.route = route

    async def request(self, method, path, **kwargs):
        result = await super().request(method, path, **kwargs)
        if path.endswith("reservations"):
            result["provider_route"] = self.route
        return result


class OtherProvider:
    """No OpenRouter HTTP, generation API or usage parser involved."""

    def __init__(self, *, fail=None):
        self.calls = []
        self.closed = False
        self.fail = fail
        self.usage = MeteredUsage(
            provider_request_id="other-123",
            input_tokens=10,
            output_tokens=2,
            cached_tokens=0,
            cost_source="stream",
        )

    def check_available(self):
        pass

    async def open(self, request, *, model_id, user_id):
        self.calls.append((model_id, user_id, request))
        if self.fail == "opening":
            raise TimeoutError("Unknown upstream execution")
        return self

    async def __aiter__(self):
        chunk = ProviderEvent(
            "other-123",
            frame={"id": "other-123", "choices": [{"delta": {"content": "Alternate provider."}}]},
        )
        yield chunk
        if self.fail == "identity":
            yield replace(chunk, generation_id="other-456")
        elif self.fail == "usage_identity":
            yield ProviderEvent(
                "other-123", usage=self.usage.model_copy(update={"provider_request_id": "wrong"})
            )
        elif self.fail == "missing_usage":
            return
        else:
            yield ProviderEvent("other-123", usage=self.usage)

    async def recover(self, generation_id, user_id):
        self.calls.append(("recover", generation_id, user_id))
        return self.usage.model_copy(update={"cost_source": "generation"})

    async def aclose(self):
        self.closed = True


def setup_service(*, fail=None, route=None):
    route = route or {
        "provider": "other",
        "provider_account_ref": "account-b",
        "provider_model_id": "different-model",
    }
    ledger = RoutedLedger(route)
    provider = OtherProvider(fail=fail)
    service = ManagedAIService(
        ledger, providers=ProviderRegistry({("other", "account-b"): provider})
    )
    return ledger, provider, service


@pytest.mark.asyncio
async def test_alternate_provider_uses_locked_model_account_and_same_metered_settlement():
    ledger, provider, service = setup_service()
    run = await service.completion("person", "other-request-0001", body())
    response = completion_response(run)
    chunks = []
    async for chunk in response.body_iterator:
        if not chunks:
            assert ledger.calls[-1][0].endswith("/provider")
        chunks.append(chunk)
    assert provider.calls[0][:2] == ("different-model", "person")
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert provider.closed
    settlement = ledger.calls[-1][1]["body"]
    assert settlement["provider"] == "other"
    assert settlement["provider_account_ref"] == "account-b"
    assert settlement["input_tokens"] == 10
    assert ledger.calls[-1][0].endswith("/settle")
    recovered = await service.recover_usage(
        "other-123", "person", provider="other", provider_account_ref="account-b"
    )
    assert recovered["cost_source"] == "generation"
    assert recovered["provider"] == "other"
    assert provider.calls[-1] == ("recover", "other-123", "person")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    [
        {
            "provider": "missing",
            "provider_account_ref": "account-b",
            "provider_model_id": "different-model",
        },
        {
            "provider": "other",
            "provider_account_ref": "wrong-account",
            "provider_model_id": "different-model",
        },
        {"provider": "other"},
    ],
)
async def test_unknown_route_releases_only_unstarted_hold_without_fallback(route):
    ledger, provider, service = setup_service(route=route)
    with pytest.raises(InferenceError):
        await service.completion("person", "unknown-route-0001", body())
    assert not provider.calls
    assert not any(path.endswith("/start") for path, _ in ledger.calls)
    assert ledger.calls[-1][1]["body"]["reason"] == "not_started"


@pytest.mark.asyncio
async def test_uncertain_open_does_not_release_or_try_another_provider():
    ledger, provider, service = setup_service(fail="opening")
    with pytest.raises(TimeoutError):
        await service.completion("person", "uncertain-request", body())
    assert len(provider.calls) == 1
    assert ledger.calls[-1][0].endswith("/start")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["identity", "usage_identity", "missing_usage"])
async def test_unverified_usage_never_settles_or_finishes(failure):
    ledger, provider, service = setup_service(fail=failure)
    response = completion_response(await service.completion("person", "invalid-usage-0001", body()))
    wire = b"".join([part async for part in response.body_iterator])
    assert b"ai_usage_pending" in wire and b"[DONE]" not in wire
    assert not any(path.endswith(("/settle", "/release")) for path, _ in ledger.calls)
    assert provider.closed


@pytest.mark.asyncio
async def test_discarded_stream_closes_provider_without_claiming_success():
    ledger, provider, service = setup_service()
    run = await service.completion("person", "discarded-request", body())
    await run.aclose()
    assert provider.closed
    assert ledger.calls[-1][0].endswith("/start")


@pytest.mark.asyncio
async def test_recovery_route_uses_the_explicit_account_and_requires_service_auth(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from src.config import settings
    from src.platform.managed_ai.dependencies import get_inference_service
    from src.platform.managed_ai.router import internal_router

    _, provider, service = setup_service()
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "fixture-service-secret")
    app = FastAPI()
    app.include_router(internal_router)
    app.dependency_overrides[get_inference_service] = lambda: service
    params = {"user_id": "person", "provider": "other", "provider_account_ref": "account-b"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/internal/ai/generations/other-123"
        assert (await client.get(path, params=params)).status_code == 403
        assert not provider.calls
        headers = {"X-Internal-Secret": "fixture-service-secret"}
        response = await client.get(path, params=params, headers=headers)
        assert response.status_code == 200
        assert response.json()["provider_account_ref"] == "account-b"
        params["provider_account_ref"] = "wrong-account"
        assert (await client.get(path, params=params, headers=headers)).status_code == 503
        assert len(provider.calls) == 1


def test_orchestration_cannot_import_http_or_concrete_providers():
    root = Path(__file__).resolve().parents[3] / "src/platform/managed_ai"
    for file in ("service.py", "contracts.py"):
        tree = ast.parse((root / file).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(
            name
            and (
                name.startswith(("httpx", "fastapi", "starlette", "src.config"))
                or ".providers.openrouter" in name
            )
            for name in imports
        )
