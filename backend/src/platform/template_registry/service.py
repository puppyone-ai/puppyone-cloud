"""Template Registry catalog and verified release application service."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .bundle import TemplateBundle, parse_template_bundle
from .config import TemplateRegistrySettings
from .exceptions import TemplateReleaseNotFoundError
from .provider import DisabledTemplateRegistryProvider, TemplatePage, TemplateRegistryProvider
from .schemas import (
    TemplateCatalog,
    TemplateDetail,
    TemplateRegistryStatus,
    TemplateRelease,
)


@dataclass(frozen=True)
class ResolvedTemplateRelease:
    template: TemplateDetail
    release: TemplateRelease
    bundle: TemplateBundle


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: object


class TemplateRegistryService:
    """Provider-neutral catalog facade with bounded metadata caching."""

    def __init__(
        self,
        *,
        provider: TemplateRegistryProvider,
        settings: TemplateRegistrySettings,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._cache: dict[tuple[object, ...], _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    def status(self) -> TemplateRegistryStatus:
        mode = self.settings.TEMPLATE_REGISTRY_MODE
        if isinstance(self.provider, DisabledTemplateRegistryProvider):
            return TemplateRegistryStatus(
                mode="disabled",
                catalog_enabled=False,
                instantiation_enabled=False,
                source="disabled",
                reason=self.provider.reason,
            )
        if mode == "remote" and self.settings.TEMPLATE_REGISTRY_REQUIRE_SIGNATURE:
            try:
                trusted_keys = self.settings.trusted_public_keys()
            except ValueError:
                trusted_keys = {}
            if not trusted_keys:
                return TemplateRegistryStatus(
                    mode="remote",
                    catalog_enabled=True,
                    instantiation_enabled=False,
                    source="remote",
                    reason="trusted_registry_key_required",
                )
        return TemplateRegistryStatus(
            mode=mode,
            catalog_enabled=True,
            instantiation_enabled=True,
            source=self.provider.source,
        )

    async def catalog(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TemplateCatalog:
        status = self.status()
        if not status.catalog_enabled:
            return TemplateCatalog(registry=status, templates=[], next_cursor=None)
        key = ("catalog", query or "", category or "", cursor or "", limit)
        page = await self._cached(
            key,
            lambda: self.provider.list_templates(
                query=query,
                category=category,
                cursor=cursor,
                limit=limit,
            ),
        )
        assert isinstance(page, TemplatePage)
        return TemplateCatalog(
            registry=status,
            templates=page.templates,
            next_cursor=page.next_cursor,
        )

    async def get_template(self, template_id: str) -> TemplateDetail:
        detail = await self._cached(
            ("detail", template_id),
            lambda: self.provider.get_template(template_id),
        )
        assert isinstance(detail, TemplateDetail)
        return detail

    async def resolve_release(
        self,
        *,
        template_id: str,
        release_id: str | None = None,
    ) -> ResolvedTemplateRelease:
        detail = await self.get_template(template_id)
        selected_id = release_id or detail.current_release.id
        release = next(
            (candidate for candidate in detail.releases if candidate.id == selected_id),
            None,
        )
        if release is None and detail.current_release.id == selected_id:
            release = detail.current_release
        if release is None:
            raise TemplateReleaseNotFoundError(
                f"release {selected_id!r} not found for template {template_id!r}"
            )

        payload = await self.provider.download_bundle(template_id, selected_id)
        is_remote = self.settings.TEMPLATE_REGISTRY_MODE == "remote"
        bundle = parse_template_bundle(
            payload=payload,
            release=release,
            expected_template_id=template_id,
            settings=self.settings,
            trusted_public_keys=self.settings.trusted_public_keys() if is_remote else {},
            require_signature=(is_remote and self.settings.TEMPLATE_REGISTRY_REQUIRE_SIGNATURE),
        )
        return ResolvedTemplateRelease(template=detail, release=release, bundle=bundle)

    async def _cached(self, key: tuple[object, ...], loader):
        ttl = self.settings.TEMPLATE_REGISTRY_CACHE_TTL_SECONDS
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value
        if ttl <= 0:
            return await loader()

        async with self._cache_lock:
            now = time.monotonic()
            entry = self._cache.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            value = await loader()
            self._cache[key] = _CacheEntry(expires_at=now + ttl, value=value)
            if len(self._cache) > 256:
                self._cache = {
                    cache_key: cached
                    for cache_key, cached in self._cache.items()
                    if cached.expires_at > now
                }
            return value
