"""Template Registry provider port and disabled implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import RegistryMode
from .exceptions import TemplateRegistryUnavailableError
from .schemas import TemplateDetail, TemplateSummary


@dataclass(frozen=True)
class TemplatePage:
    templates: list[TemplateSummary]
    next_cursor: str | None = None


class TemplateRegistryProvider(Protocol):
    mode: RegistryMode
    source: RegistryMode

    async def list_templates(
        self,
        *,
        query: str | None,
        category: str | None,
        cursor: str | None,
        limit: int,
    ) -> TemplatePage: ...

    async def get_template(self, template_id: str) -> TemplateDetail: ...

    async def download_bundle(self, template_id: str, release_id: str) -> bytes: ...


class DisabledTemplateRegistryProvider:
    mode: RegistryMode = "disabled"
    source: RegistryMode = "disabled"

    def __init__(self, reason: str = "registry_disabled") -> None:
        self.reason = reason

    async def list_templates(
        self,
        *,
        query: str | None,
        category: str | None,
        cursor: str | None,
        limit: int,
    ) -> TemplatePage:
        del query, category, cursor, limit
        return TemplatePage(templates=[])

    async def get_template(self, template_id: str) -> TemplateDetail:
        del template_id
        raise TemplateRegistryUnavailableError(self.reason)

    async def download_bundle(self, template_id: str, release_id: str) -> bytes:
        del template_id, release_id
        raise TemplateRegistryUnavailableError(self.reason)
