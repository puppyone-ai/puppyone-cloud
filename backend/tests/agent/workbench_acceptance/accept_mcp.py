"""MCP product gate against owned SQL/object storage, without resolver stubs.

Invoke explicitly alongside accept_production_entry.py. An issued endpoint is
not a successful connection: discovery, file access and revocation must work.
This prerequisite does not substitute for external Streamable HTTP transport.
"""

import pytest

from src.config import settings
from tests.agent.workbench_acceptance.accept_production_entry import (
    control_api as control_api,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    entry as entry,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    postgres as postgres,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    services as services,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_issued_project_mcp_key_can_discover_read_and_be_revoked(entry):
    created = await entry.http.post(
        "/api/v1/mcp-endpoints",
        json={
            "project_id": entry.project,
            "name": "Acceptance MCP",
            "path": "",
            "accesses": [{"path": "", "json_path": "", "readonly": True}],
        },
    )
    assert created.status_code in (200, 201), created.text
    endpoint = created.json()["data"]
    key = endpoint["api_key"]
    assert key.startswith("mcp_")
    internal = {"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    try:
        tools = await entry.http.post(
            "/internal/mcp-runtime/tools", headers=internal, json={"api_key": key}
        )
        assert tools.status_code == 200, tools.text
        names = {tool["name"] for tool in tools.json()["tools"]}
        assert "fs_cat" in names and "fs_write" not in names
        read = await entry.http.post(
            "/internal/mcp-runtime/call",
            headers=internal,
            json={"api_key": key, "name": "fs_cat", "arguments": {"path": "seed.txt"}},
        )
        assert read.status_code == 200, read.text
        assert "seed" in read.json()["structuredContent"].values()
        denied = await entry.http.post(
            "/internal/mcp-runtime/call",
            headers=internal,
            json={
                "api_key": key,
                "name": "fs_write",
                "arguments": {"path": "seed.txt", "content": "forbidden"},
            },
        )
        assert denied.status_code == 403, denied.text
    finally:
        deleted = await entry.http.delete("/api/v1/mcp-endpoints/" + endpoint["id"])
        assert deleted.status_code in (200, 204), deleted.text
    revoked = await entry.http.post(
        "/internal/mcp-runtime/tools", headers=internal, json={"api_key": key}
    )
    assert revoked.status_code in (401, 404), revoked.text
