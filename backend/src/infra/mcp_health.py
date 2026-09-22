"""Transport-only health probe for the external MCP protocol service."""

from __future__ import annotations

from typing import Any

import httpx

from src.config import settings


class McpHealthClient:
    async def check_mcp_server_health(self) -> dict[str, Any]:
        base = (settings.MCP_SERVER_URL or "").rstrip("/")
        if not base:
            return {"status": "unhealthy", "error": "MCP_SERVER_URL is empty"}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                response = await client.get(f"{base}/healthz")
            if response.status_code == 200:
                return response.json()
            return {"status": "unhealthy", "error": f"HTTP {response.status_code}"}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}


_client = McpHealthClient()


def get_mcp_health_client() -> McpHealthClient:
    return _client
