"""Read-only HTTP client for the external Template Registry v1 protocol."""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from ..config import RegistryMode, TemplateRegistrySettings
from ..exceptions import (
    TemplateBundleTooLargeError,
    TemplateNotFoundError,
    TemplateRegistryUpstreamError,
    TemplateReleaseNotFoundError,
)
from ..provider import TemplatePage
from ..schemas import RemoteTemplateCatalog, TemplateDetail


class RemoteTemplateRegistryProvider:
    mode: RegistryMode = "remote"
    source: RegistryMode = "remote"

    def __init__(
        self,
        settings: TemplateRegistrySettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.TEMPLATE_REGISTRY_URL.rstrip("/") + "/"
        self._transport = transport

    async def list_templates(
        self,
        *,
        query: str | None,
        category: str | None,
        cursor: str | None,
        limit: int,
    ) -> TemplatePage:
        params = {"limit": str(limit)}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if cursor:
            params["cursor"] = cursor
        payload = await self._get_json("v1/templates", params=params)
        try:
            catalog = RemoteTemplateCatalog.model_validate(payload)
        except ValidationError as exc:
            raise TemplateRegistryUpstreamError("Registry catalog response is invalid") from exc
        if len(catalog.templates) > limit:
            raise TemplateRegistryUpstreamError(
                "Registry catalog returned more templates than requested"
            )
        return TemplatePage(templates=catalog.templates, next_cursor=catalog.next_cursor)

    async def get_template(self, template_id: str) -> TemplateDetail:
        encoded = quote(template_id, safe="")
        payload = await self._get_json(f"v1/templates/{encoded}", not_found="template")
        try:
            return TemplateDetail.model_validate(payload)
        except ValidationError as exc:
            raise TemplateRegistryUpstreamError("Registry template response is invalid") from exc

    async def download_bundle(self, template_id: str, release_id: str) -> bytes:
        template = quote(template_id, safe="")
        release = quote(release_id, safe="")
        path = f"v1/templates/{template}/releases/{release}/bundle"
        chunks: list[bytes] = []
        size = 0
        try:
            async with (
                self._client() as client,
                client.stream("GET", path) as response,
            ):
                if response.status_code == 404:
                    raise TemplateReleaseNotFoundError(
                        f"release {release_id!r} not found for template {template_id!r}"
                    )
                if response.status_code != 200:
                    raise TemplateRegistryUpstreamError(
                        f"Registry bundle request failed with status {response.status_code}"
                    )
                content_length = _content_length(response)
                if (
                    content_length is not None
                    and content_length > self._settings.TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES
                ):
                    raise TemplateBundleTooLargeError(
                        "Registry bundle exceeds compressed byte limit"
                    )
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._settings.TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES:
                        raise TemplateBundleTooLargeError(
                            "Registry bundle exceeds compressed byte limit"
                        )
                    chunks.append(chunk)
        except (
            TemplateBundleTooLargeError,
            TemplateReleaseNotFoundError,
            TemplateRegistryUpstreamError,
        ):
            raise
        except httpx.HTTPError as exc:
            raise TemplateRegistryUpstreamError("Unable to reach Template Registry") from exc
        return b"".join(chunks)

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        not_found: str | None = None,
    ) -> object:
        try:
            async with (
                self._client() as client,
                client.stream("GET", path, params=params) as response,
            ):
                if response.status_code == 404 and not_found == "template":
                    raise TemplateNotFoundError("template not found")
                if response.status_code != 200:
                    raise TemplateRegistryUpstreamError(
                        f"Registry request failed with status {response.status_code}"
                    )
                maximum = self._settings.TEMPLATE_REGISTRY_MAX_METADATA_BYTES
                content_length = _content_length(response)
                if content_length is not None and content_length > maximum:
                    raise TemplateRegistryUpstreamError(
                        "Registry metadata response exceeds byte limit"
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > maximum:
                        raise TemplateRegistryUpstreamError(
                            "Registry metadata response exceeds byte limit"
                        )
                    chunks.append(chunk)
        except (TemplateNotFoundError, TemplateRegistryUpstreamError):
            raise
        except httpx.HTTPError as exc:
            raise TemplateRegistryUpstreamError("Unable to reach Template Registry") from exc
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TemplateRegistryUpstreamError("Registry returned invalid JSON") from exc

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._settings.TEMPLATE_REGISTRY_TIMEOUT_SECONDS),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
            headers={
                "Accept": "application/json, application/zip;q=0.9",
                "User-Agent": "papertrain-template-registry-client/1",
            },
        )


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise TemplateRegistryUpstreamError("Registry returned an invalid Content-Length") from None
    if parsed < 0:
        raise TemplateRegistryUpstreamError("Registry returned an invalid Content-Length")
    return parsed
