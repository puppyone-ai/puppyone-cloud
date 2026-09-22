from __future__ import annotations

import httpx
import pytest

from src.platform.template_registry.config import TemplateRegistrySettings
from src.platform.template_registry.exceptions import (
    TemplateBundleTooLargeError,
    TemplateRegistryUpstreamError,
)
from src.platform.template_registry.providers.remote import (
    RemoteTemplateRegistryProvider,
)


def _settings(**overrides) -> TemplateRegistrySettings:
    values = {
        "TEMPLATE_REGISTRY_MODE": "remote",
        "TEMPLATE_REGISTRY_URL": "http://localhost:8765/registry",
        "TEMPLATE_REGISTRY_REQUIRE_SIGNATURE": False,
    }
    values.update(overrides)
    return TemplateRegistrySettings(_env_file=None, **values)


def _summary() -> dict:
    return {
        "id": "hello",
        "name": "Hello",
        "description": "A portable starter",
        "current_release": {
            "id": "1.0.0",
            "version": "1.0.0",
            "bundle_sha256": "a" * 64,
            "file_count": 1,
            "total_bytes": 5,
        },
    }


@pytest.mark.asyncio
async def test_remote_provider_uses_fixed_origin_and_validates_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/registry/v1/templates"
        assert request.url.params["q"] == "hello"
        assert request.url.params["limit"] == "10"
        return httpx.Response(200, json={"templates": [_summary()], "next_cursor": None})

    provider = RemoteTemplateRegistryProvider(_settings(), transport=httpx.MockTransport(handler))
    page = await provider.list_templates(query="hello", category=None, cursor=None, limit=10)

    assert [item.id for item in page.templates] == ["hello"]


@pytest.mark.asyncio
async def test_remote_provider_rejects_redirects() -> None:
    provider = RemoteTemplateRegistryProvider(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                302, headers={"location": "https://untrusted.example/bundle"}
            )
        ),
    )

    with pytest.raises(TemplateRegistryUpstreamError, match="status 302"):
        await provider.download_bundle("hello", "1.0.0")


@pytest.mark.asyncio
async def test_remote_provider_rejects_oversized_bundle_from_headers() -> None:
    provider = RemoteTemplateRegistryProvider(
        _settings(TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES=1024),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-length": "2048"},
                content=b"small-response",
            )
        ),
    )

    with pytest.raises(TemplateBundleTooLargeError):
        await provider.download_bundle("hello", "1.0.0")


@pytest.mark.asyncio
async def test_remote_provider_rejects_unknown_json_fields() -> None:
    payload = {"templates": [_summary()], "next_cursor": None, "surprise": True}
    provider = RemoteTemplateRegistryProvider(
        _settings(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(TemplateRegistryUpstreamError, match="invalid"):
        await provider.list_templates(query=None, category=None, cursor=None, limit=10)


@pytest.mark.asyncio
async def test_remote_provider_enforces_the_requested_page_size() -> None:
    payload = {"templates": [_summary(), {**_summary(), "id": "second"}]}
    provider = RemoteTemplateRegistryProvider(
        _settings(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(TemplateRegistryUpstreamError, match="more templates"):
        await provider.list_templates(query=None, category=None, cursor=None, limit=1)


@pytest.mark.asyncio
async def test_remote_provider_bounds_streamed_metadata_bytes() -> None:
    provider = RemoteTemplateRegistryProvider(
        _settings(TEMPLATE_REGISTRY_MAX_METADATA_BYTES=64 * 1024),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b" " * (64 * 1024 + 1))
        ),
    )

    with pytest.raises(TemplateRegistryUpstreamError, match="exceeds byte limit"):
        await provider.list_templates(query=None, category=None, cursor=None, limit=10)


@pytest.mark.asyncio
async def test_remote_provider_rejects_invalid_content_length() -> None:
    provider = RemoteTemplateRegistryProvider(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-length": "not-a-number"},
                content=b"{}",
            )
        ),
    )

    with pytest.raises(TemplateRegistryUpstreamError, match="Content-Length"):
        await provider.list_templates(query=None, category=None, cursor=None, limit=10)
