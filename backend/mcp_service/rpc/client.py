"""Minimal RPC adapter from MCP transport to the canonical backend runtime."""

from __future__ import annotations

from typing import Any

import httpx


class InternalApiClient:
    """The transport has no product/data methods of its own."""

    def __init__(self, base_url: str, secret: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"X-Internal-Secret": secret},
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.close()

    async def list_mcp_runtime_tools(self, api_key: str) -> dict[str, Any]:
        response = await self._client.post(
            f"{self.base_url}/internal/mcp-runtime/tools",
            json={"api_key": api_key},
        )
        response.raise_for_status()
        return response.json()

    async def call_mcp_runtime_tool(
        self,
        api_key: str,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"{self.base_url}/internal/mcp-runtime/call",
            json={"api_key": api_key, "name": name, "arguments": arguments or {}},
        )
        if response.status_code < 400:
            return response.json()
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if isinstance(body, dict):
            detail = body.get("data") or body.get("detail") or body.get("message") or body
        else:
            detail = body
        return {
            "isError": True,
            "error": detail,
            "status_code": response.status_code,
        }


def create_client() -> InternalApiClient:
    from ..settings import settings

    return InternalApiClient(
        base_url=settings.MAIN_SERVICE_URL,
        secret=settings.INTERNAL_API_SECRET,
        timeout=settings.RPC_TIMEOUT,
    )
