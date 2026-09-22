from __future__ import annotations

import pytest

from src.platform.template_registry.config import TemplateRegistrySettings
from src.platform.template_registry.provider import DisabledTemplateRegistryProvider
from src.platform.template_registry.providers.remote import RemoteTemplateRegistryProvider
from src.platform.template_registry.service import TemplateRegistryService


def _settings(**overrides) -> TemplateRegistrySettings:
    values = {
        "TEMPLATE_REGISTRY_MODE": "remote",
        "TEMPLATE_REGISTRY_URL": "https://registry.example.test",
    }
    values.update(overrides)
    return TemplateRegistrySettings(_env_file=None, **values)


def test_remote_catalog_remains_visible_but_instantiation_requires_a_trusted_key() -> None:
    settings = _settings(
        TEMPLATE_REGISTRY_REQUIRE_SIGNATURE=True,
        TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON="{}",
    )
    service = TemplateRegistryService(
        provider=RemoteTemplateRegistryProvider(settings),
        settings=settings,
    )

    status = service.status()

    assert status.catalog_enabled is True
    assert status.instantiation_enabled is False
    assert status.reason == "trusted_registry_key_required"


@pytest.mark.asyncio
async def test_disabled_provider_returns_an_empty_catalog_without_external_io() -> None:
    settings = TemplateRegistrySettings(
        _env_file=None,
        TEMPLATE_REGISTRY_MODE="disabled",
    )
    service = TemplateRegistryService(
        provider=DisabledTemplateRegistryProvider(),
        settings=settings,
    )

    catalog = await service.catalog(limit=10)

    assert catalog.registry.catalog_enabled is False
    assert catalog.templates == []
    assert catalog.next_cursor is None


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.example.test",
        "https://user:password@registry.example.test",
        "https://registry.example.test?token=secret",
    ],
)
def test_remote_registry_configuration_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        _settings(TEMPLATE_REGISTRY_URL=url)


def test_trusted_registry_keys_require_strict_base64() -> None:
    with pytest.raises(ValueError, match="valid base64"):
        _settings(
            TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON=(
                '{"key":"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!="}'
            ),
        )
