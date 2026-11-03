"""OpenRouter is one profile; other OpenAI-compatible routes are configuration."""

import json
import logging
from importlib import import_module

import httpx
import pytest
from pydantic import ValidationError

from src.config import Settings
from src.platform.managed_ai.contracts import InferenceError
from src.platform.managed_ai.providers.base import ProviderRejected
from src.platform.managed_ai.providers.openai_compatible import OpenAICompatibleProvider
from src.platform.managed_ai.providers.openrouter import OPENROUTER, OpenRouterProvider
from src.platform.managed_ai.providers.registry import ProviderRegistry
from src.platform.managed_ai.service import ManagedAIService
from src.shared_types.managed_ai_provider import (
    ManagedAIProviderProfile,
    parse_provider_profiles,
)
from tests.platform.managed_ai.test_provider_boundary import RoutedLedger
from tests.platform.managed_ai.test_service import body, event, provider_user

USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 8,
    "prompt_tokens_details": {"cached_tokens": 3},
}


def profile(**changes):
    values = {
        "provider": "example",
        "base_url": "https://api.example.com/v1/",
        "api_key_env": "MANAGED_AI_EXAMPLE_KEY",
    }
    return ManagedAIProviderProfile.model_validate({**values, **changes})


def streaming_transport(usage=USAGE, captured=None):
    def handler(request):
        if captured is not None:
            captured.append(request)
        return httpx.Response(
            200,
            text=event(choices=[{"delta": {"content": "hello"}}])
            + event(usage=usage)
            + "data: [DONE]\n\n",
        )

    return httpx.MockTransport(handler)


async def drain(provider, **kwargs):
    stream = await provider.open(body(**kwargs), model_id="upstream/model", user_id="user-one")
    try:
        return [item async for item in stream]
    finally:
        await stream.aclose()


async def test_openrouter_profile_keeps_its_exact_request():
    captured = []
    provider = OpenRouterProvider(
        key="server-key", transport=streaming_transport(captured=captured)
    )

    events = await drain(provider)

    request = captured[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer server-key"
    assert request.headers["http-referer"] == "https://puppyone.ai"
    assert request.headers["x-title"] == "PuppyOne Desktop Agent"
    sent = json.loads(request.content)
    assert sent["model"] == "upstream/model"
    assert sent["user"] == provider_user("user-one")
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["max_tokens"] == body().max_tokens
    assert events[-1].usage.cached_tokens == 3


async def test_configured_route_uses_its_own_url_key_and_model():
    captured = []
    provider = OpenAICompatibleProvider(
        profile(), key="other-key", transport=streaming_transport(captured=captured)
    )

    events = await drain(provider)

    request = captured[0]
    assert str(request.url) == "https://api.example.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer other-key"
    assert "http-referer" not in request.headers
    assert "x-title" not in request.headers
    assert json.loads(request.content)["model"] == "upstream/model"
    assert (events[-1].usage.input_tokens, events[-1].usage.output_tokens) == (12, 8)


async def test_dialect_changes_only_the_outgoing_fields():
    captured = []
    provider = OpenAICompatibleProvider(
        profile(
            drop_fields=["reasoning_details", "parallel_tool_calls"],
            rename_fields={"max_tokens": "max_completion_tokens"},
        ),
        key="key",
        transport=streaming_transport(captured=captured),
    )
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "ok",
            "reasoning_details": [{"type": "reasoning.text", "text": "because"}],
        },
    ]

    await drain(provider, messages=messages, parallel_tool_calls=True, max_tokens=64)

    sent = json.loads(captured[0].content)
    assert sent["max_completion_tokens"] == 64
    assert "max_tokens" not in sent
    assert "parallel_tool_calls" not in sent
    assert "reasoning_details" not in sent["messages"][1]
    assert sent["messages"][1]["content"] == "ok"


@pytest.mark.parametrize(
    ("configured", "usage", "expected"),
    [
        ("usage_details.cache_hit", {**USAGE, "usage_details": {"cache_hit": 5}}, 5),
        (0, {"prompt_tokens": 12, "completion_tokens": 8}, 0),
    ],
)
async def test_cached_tokens_come_from_the_configured_location(configured, usage, expected):
    provider = OpenAICompatibleProvider(
        profile(cached_tokens=configured), key="key", transport=streaming_transport(usage)
    )

    events = await drain(provider)

    assert events[-1].usage.cached_tokens == expected


async def test_unreported_cache_usage_is_never_read_as_zero():
    provider = OpenAICompatibleProvider(
        profile(),
        key="key",
        transport=streaming_transport({"prompt_tokens": 12, "completion_tokens": 8}),
    )

    with pytest.raises(ValueError):
        await drain(provider)


async def test_configured_route_settles_through_the_same_ledger():
    ledger = RoutedLedger(
        {
            "provider": "example",
            "provider_account_ref": "managed-default",
            "provider_model_id": "upstream/model",
        }
    )
    captured = []
    adapter = OpenAICompatibleProvider(
        profile(), key="key", transport=streaming_transport(captured=captured)
    )
    service = ManagedAIService(
        ledger, providers=ProviderRegistry({("example", "managed-default"): adapter})
    )

    run = await service.completion("user-one", "unique-request-0001", body())
    outcomes = [item async for item in run.events]

    assert json.loads(captured[0].content)["model"] == "upstream/model"
    assert type(outcomes[-1]).__name__ == "InferenceDone"
    settle = ledger.calls[-1]
    assert settle[0].endswith("/settle")
    assert settle[1]["body"]["provider"] == "example"
    assert settle[1]["body"]["cached_tokens"] == 3


async def test_route_without_usage_lookup_keeps_the_hold_without_calling_out():
    def refuse(_request):
        raise AssertionError("A route without usage lookup must not call out")

    provider = OpenAICompatibleProvider(profile(), key="key", transport=httpx.MockTransport(refuse))

    with pytest.raises(InferenceError) as failure:
        await provider.recover("gen-test", "user-one")

    assert (failure.value.status_code, failure.value.code) == (503, "ai_usage_pending")


@pytest.mark.parametrize(
    ("status", "error"), [(402, ProviderRejected), (429, ProviderRejected), (500, InferenceError)]
)
async def test_upstream_status_is_logged_without_body_or_key(status, error, caplog):
    provider = OpenAICompatibleProvider(
        profile(),
        key="secret-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="customer prompt")),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(error):
        await provider.open(body(), model_id="upstream/model", user_id="user-one")

    record = next(r for r in caplog.records if r.message == "managed_ai_provider_request_failed")
    assert (record.provider, record.status_code) == ("example", status)
    assert "secret-key" not in caplog.text
    assert "customer prompt" not in caplog.text


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://api.example.com/v1"},
        {"base_url": "api.example.com/v1"},
        {"base_url": "https://user:secret@api.example.com/v1"},
        {"base_url": "https://api.example.com/v1?key=secret"},
        {"api_key_env": "sk-or-v1-a-real-key-pasted-here"},
        {"api_key": "secret"},
        {"headers": {"Authorization": "Bearer secret"}},
        {"headers": {"X-API-Key": "secret"}},
        {"cached_tokens": 5},
        {"cached_tokens": "not a path"},
        {"drop_fields": ["messages"]},
        {"drop_fields": ["tool_calls"]},
        {"rename_fields": {"model": "engine"}},
        {"rename_fields": {"max_tokens": "stream"}},
    ],
)
def test_unsafe_profiles_are_refused(changes):
    with pytest.raises(ValidationError):
        profile(**changes)


def test_validation_errors_do_not_echo_configured_values():
    with pytest.raises(ValidationError) as failure:
        profile(api_key_env="sk-or-v1-a-real-key-pasted-here")

    assert "sk-or-v1" not in str(failure.value)


def test_loopback_may_use_http_for_local_gateways():
    assert profile(base_url="http://127.0.0.1:4000/v1").base_url == "http://127.0.0.1:4000/v1"


def test_openrouter_is_a_plain_profile():
    assert OPENROUTER.route == ("openrouter", "managed-default")
    assert OPENROUTER.base_url == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize("blank", [None, "", "   ", "[]"])
def test_blank_configuration_adds_no_routes(blank):
    assert parse_provider_profiles(blank) == ()


ENTRY = {"provider": "example", "base_url": "https://a.test/v1", "api_key_env": "EXAMPLE_KEY"}


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps(ENTRY),
        json.dumps([ENTRY, ENTRY]),
        json.dumps([{**ENTRY, "provider": "openrouter"}]),
    ],
)
def test_invalid_configuration_is_refused(raw):
    with pytest.raises(ValueError):
        parse_provider_profiles(raw)


def test_settings_read_routes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MANAGED_AI_PROVIDERS", json.dumps([ENTRY]))

    routes = Settings(_env_file=None).MANAGED_AI_PROVIDERS

    assert [route.route for route in routes] == [("example", "managed-default")]


@pytest.mark.parametrize("raw", ["not json", json.dumps([{**ENTRY, "base_url": "http://a.test"}])])
def test_settings_refuse_to_start_with_an_invalid_route(monkeypatch, raw):
    monkeypatch.setenv("MANAGED_AI_PROVIDERS", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_blank_environment_value_keeps_the_default(monkeypatch):
    monkeypatch.setenv("MANAGED_AI_PROVIDERS", "")

    assert Settings(_env_file=None).MANAGED_AI_PROVIDERS == ()


def registry_with(monkeypatch, routes):
    dependencies = import_module("src.platform.managed_ai.dependencies")
    monkeypatch.setattr(dependencies.settings, "MANAGED_AI_PROVIDERS", routes)
    monkeypatch.setattr(dependencies.settings, "MANAGED_AI_OPENROUTER_KEY", "openrouter-key")
    return dependencies.get_provider_registry()


def test_registry_keeps_openrouter_and_adds_configured_routes(monkeypatch):
    monkeypatch.setenv("MANAGED_AI_EXAMPLE_KEY", "example-key")

    registry = registry_with(monkeypatch, (profile(),))

    assert isinstance(registry.resolve("openrouter", "managed-default"), OpenRouterProvider)
    assert type(registry.resolve("example", "managed-default")) is OpenAICompatibleProvider


def test_default_registry_has_only_openrouter(monkeypatch):
    registry = registry_with(monkeypatch, ())

    with pytest.raises(InferenceError) as failure:
        registry.resolve("example", "managed-default")

    assert failure.value.code == "ai_route_unavailable"


def test_route_without_its_key_is_unconfigured(monkeypatch):
    monkeypatch.delenv("MANAGED_AI_EXAMPLE_KEY", raising=False)

    registry = registry_with(monkeypatch, (profile(),))

    with pytest.raises(InferenceError) as failure:
        registry.resolve("example", "managed-default")

    assert failure.value.code == "ai_provider_unconfigured"
