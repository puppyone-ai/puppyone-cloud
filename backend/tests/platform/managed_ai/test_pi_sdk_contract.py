"""Opt-in cross-repository check against the actual Desktop SDK serialization."""

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.billing.gateway import get_billing_gateway
from src.platform.managed_ai import router as routes
from src.platform.managed_ai.dependencies import get_provider_registry
from src.platform.managed_ai.providers.openrouter import OpenRouterProvider
from src.platform.managed_ai.providers.registry import ProviderRegistry
from tests.platform.managed_ai.test_router import user
from tests.platform.managed_ai.test_service import Ledger, event


@pytest.mark.asyncio
async def test_pinned_desktop_sdk_roundtrips_through_real_gateway(monkeypatch):
    desktop = os.environ.get("PUPPYONE_DESKTOP_REPO")
    if not desktop:
        pytest.skip("Set PUPPYONE_DESKTOP_REPO to run the pinned Desktop SDK contract")
    capture = Path(__file__).with_name("capture_pi_requests.mjs")
    process = subprocess.run(
        ["node", str(capture), desktop], check=True, capture_output=True, text=True, timeout=30
    )
    cases = json.loads(process.stdout)
    assert len(cases) == 16
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = user
    ledger = Ledger()
    app.dependency_overrides[get_billing_gateway] = lambda: ledger
    forwarded = []

    def provider(request):
        forwarded.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=event(choices=[{"delta": {"content": "Accepted."}}])
            + event(
                usage={
                    "prompt_tokens": 20,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 0},
                }
            )
            + "data: [DONE]\n\n",
        )

    app.dependency_overrides[get_provider_registry] = lambda: ProviderRegistry(
        {
            ("openrouter", "managed-default"): OpenRouterProvider(
                key="synthetic", transport=httpx.MockTransport(provider)
            ),
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for index, case in enumerate(cases):
            ledger.started = False
            result = await client.post(
                "/api/v1/ai/chat/completions",
                json=case["body"],
                headers={"Idempotency-Key": f"sdk-contract-request-{index:02}"},
            )
            assert result.status_code == 200, (
                case["field"],
                case["tool"],
                case["round"],
                result.text,
            )
            assert result.text.endswith("data: [DONE]\n\n")
            expected = [
                {key: value for key, value in message.items() if value is not None}
                for message in case["body"]["messages"]
            ]
            assert forwarded[-1]["messages"] == expected
            assert ledger.calls[-1][0].endswith("/settle")
        invalid = await client.post(
            "/api/v1/ai/chat/completions",
            json={"model": "test/model", "messages": [{"role": "assistant", "reasoning": {}}]},
            headers={"Idempotency-Key": "sdk-contract-invalid-request"},
        )
        assert invalid.status_code == 422
        consumed = subprocess.run(
            ["node", str(capture), desktop, "--error"],
            input=invalid.text,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(consumed.stdout) == {"readable": True, "requests": 1}
    assert len(forwarded) == 16
