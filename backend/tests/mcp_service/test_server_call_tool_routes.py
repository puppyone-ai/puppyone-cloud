"""The MCP transport delegates every surface kind to the canonical runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

import mcp_service.server as server_module
from mcp_service.settings import Settings, settings as mcp_settings


class _FakeMcpServer:
    latest = None

    def __init__(self, _name: str):
        self.request_context = None
        self._list_tools_fn = None
        self._call_tool_fn = None
        _FakeMcpServer.latest = self

    def list_tools(self):
        return lambda fn: self._capture("_list_tools_fn", fn)

    def call_tool(self):
        return lambda fn: self._capture("_call_tool_fn", fn)

    def _capture(self, attr, fn):
        setattr(self, attr, fn)
        return fn


class _FakeSessionManager:
    def __init__(self, **_kwargs):
        pass

    async def handle_request(self, **_kwargs):
        return None

    class _RunCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    def run(self):
        return self._RunCtx()


class _FakeSessions:
    bind = AsyncMock(return_value=None)
    bind_surface = AsyncMock(return_value=None)
    notify_tools_list_changed = AsyncMock(return_value=1)
    notify_surface_changed = AsyncMock(return_value=1)
    broadcast_tools_list_changed = AsyncMock(return_value=1)
    broadcast_surface_changed = AsyncMock(return_value=1)
    start = AsyncMock(return_value=None)
    close = AsyncMock(return_value=None)


class _FakeRpc:
    def __init__(self):
        self.list_mcp_runtime_tools = AsyncMock(return_value={
            "mode": "agent",
            "endpoint": {"id": "agent-1"},
            "tools": [{
                "name": "fs_ls",
                "title": "List Directory",
                "description": "List files",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True},
            }],
        })
        self.call_mcp_runtime_tool = AsyncMock(return_value={
            "structuredContent": {"entries": [{"path": "README.md"}]},
        })
        self.close = AsyncMock()


@pytest.fixture
def server_env(monkeypatch):
    fake_rpc = _FakeRpc()
    monkeypatch.setattr(server_module, "MCP_Server", _FakeMcpServer)
    monkeypatch.setattr(server_module, "StreamableHTTPSessionManager", _FakeSessionManager)
    monkeypatch.setattr(server_module, "SessionRegistry", _FakeSessions)
    monkeypatch.setattr(server_module, "create_client", lambda: fake_rpc)
    monkeypatch.setattr(server_module, "extract_api_key", lambda _req: "mcp_key")
    app = server_module.build_starlette_app()
    fake_server = _FakeMcpServer.latest
    fake_server.request_context = SimpleNamespace(request=object(), session=object())
    return app, fake_server, fake_rpc


@pytest.mark.asyncio
@pytest.mark.parametrize("surface_kind", ["agent", "mcp"])
async def test_list_tools_all_surface_kinds_use_runtime(server_env, surface_kind):
    _app, fake_server, rpc = server_env
    rpc.list_mcp_runtime_tools.return_value["mode"] = surface_kind
    tools = await fake_server._list_tools_fn()
    assert [tool.name for tool in tools] == ["fs_ls"]
    rpc.list_mcp_runtime_tools.assert_awaited_once_with("mcp_key")


@pytest.mark.asyncio
@pytest.mark.parametrize("surface_kind", ["agent", "mcp"])
async def test_call_tool_all_surface_kinds_use_runtime(server_env, surface_kind):
    _app, fake_server, rpc = server_env
    rpc.list_mcp_runtime_tools.return_value["mode"] = surface_kind
    result = await fake_server._call_tool_fn("fs_ls", {"path": ""})
    assert result["entries"][0]["path"] == "README.md"
    rpc.call_mcp_runtime_tool.assert_awaited_once_with("mcp_key", "fs_ls", {"path": ""})


@pytest.mark.asyncio
async def test_runtime_error_is_returned_as_mcp_error(server_env):
    _app, fake_server, rpc = server_env
    rpc.call_mcp_runtime_tool.return_value = {
        "isError": True,
        "error": {"code": "PERMISSION_DENIED", "message": "readonly"},
    }
    result = await fake_server._call_tool_fn("fs_write", {})
    assert result.isError is True
    assert "PERMISSION_DENIED" in result.content[0].text


def test_cache_invalidation_requires_internal_secret(server_env, monkeypatch):
    app, _server, _rpc = server_env
    monkeypatch.setattr(mcp_settings, "INTERNAL_API_SECRET", "shared-secret")
    with TestClient(app) as client:
        assert client.post("/cache/invalidate", json={}).status_code == 401
        assert client.post(
            "/cache/invalidate",
            json={},
            headers={"X-Internal-Secret": "shared-secret"},
        ).status_code == 400


def test_hosted_cors_rejects_wildcard():
    hosted = Settings(
        APP_ENV="production",
        INTERNAL_API_SECRET="secret",
        REDIS_URL="redis://example.invalid:6379/0",
        CORS_ALLOWED_ORIGINS="*",
    )
    with pytest.raises(ValueError, match="explicit allowlist"):
        hosted.validate()
