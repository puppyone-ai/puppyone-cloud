from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.internal import mcp_runtime
from src.version_engine.scoped_fs.errors import ScopedFsPermissionDenied
from src.version_engine.scoped_fs.context import ScopedFsContext
from src.version_engine.scoped_fs import service as scoped_fs_service
from src.version_engine.scoped_fs.resolver import ResolvedMcpRuntime
from src.version_engine.scoped_fs.service import ScopedFsService


def _ctx(mode: str) -> ScopedFsContext:
    return ScopedFsContext(
        api_key="mcp_key",
        endpoint_id="endpoint-1",
        endpoint_name="Docs MCP",
        project_id="proj-1",
        user_id="user-1",
        scope_id="scope-1",
        scope_path="docs",
        mode=mode,
        exclude=["private"],
    )


def _ctx_with_tools(mode: str, allowed_tools: frozenset[str]) -> ScopedFsContext:
    ctx = _ctx(mode)
    return ScopedFsContext(
        api_key=ctx.api_key,
        endpoint_id=ctx.endpoint_id,
        endpoint_name=ctx.endpoint_name,
        project_id=ctx.project_id,
        user_id=ctx.user_id,
        scope_id=ctx.scope_id,
        scope_path=ctx.scope_path,
        mode=ctx.mode,
        exclude=ctx.exclude,
        allowed_tools=allowed_tools,
    )


def _runtime(ctx: ScopedFsContext, kind: str = "mcp") -> ResolvedMcpRuntime:
    return ResolvedMcpRuntime(
        context=ctx,
        surface_kind=kind,
        surface={"id": ctx.endpoint_id, "kind": kind},
    )


@pytest.mark.asyncio
async def test_runtime_tools_list_returns_complete_mcp_contract(monkeypatch):
    monkeypatch.setattr(mcp_runtime, "resolve_mcp_runtime", lambda _key: _runtime(_ctx("rw")))
    monkeypatch.setattr(mcp_runtime, "_list_custom_tools", lambda _runtime: [])

    result = await mcp_runtime.list_runtime_tools(mcp_runtime.RuntimeToolsRequest(api_key="mcp_key"))
    tools = {tool["name"]: tool for tool in result["tools"]}

    assert result["mode"] == "mcp"
    assert result["endpoint"]["scope_path"] == "docs"
    assert "fs_ls" in tools
    assert "fs_write" in tools
    assert tools["fs_ls"]["title"] == "List Directory"
    assert tools["fs_ls"]["inputSchema"]["type"] == "object"
    assert tools["fs_ls"]["outputSchema"]["type"] == "object"
    assert tools["fs_ls"]["annotations"]["readOnlyHint"] is True
    assert tools["fs_write"]["annotations"]["readOnlyHint"] is False


@pytest.mark.asyncio
async def test_runtime_tools_list_hides_mutations_for_readonly_endpoint(monkeypatch):
    monkeypatch.setattr(mcp_runtime, "resolve_mcp_runtime", lambda _key: _runtime(_ctx("ro")))
    monkeypatch.setattr(mcp_runtime, "_list_custom_tools", lambda _runtime: [])

    result = await mcp_runtime.list_runtime_tools(mcp_runtime.RuntimeToolsRequest(api_key="mcp_key"))
    names = {tool["name"] for tool in result["tools"]}

    assert "fs_ls" in names
    assert "fs_write" not in names
    assert "fs_rm" not in names


@pytest.mark.asyncio
async def test_runtime_tools_list_respects_endpoint_tool_policy(monkeypatch):
    monkeypatch.setattr(
        mcp_runtime,
        "resolve_mcp_runtime",
        lambda _key: _runtime(_ctx_with_tools("rw", frozenset({"fs_ls", "fs_rm"}))),
    )
    monkeypatch.setattr(mcp_runtime, "_list_custom_tools", lambda _runtime: [])

    result = await mcp_runtime.list_runtime_tools(mcp_runtime.RuntimeToolsRequest(api_key="mcp_key"))
    names = {tool["name"] for tool in result["tools"]}

    assert names == {"fs_ls", "fs_rm"}


@pytest.mark.asyncio
async def test_runtime_call_rejects_disabled_tool_before_execution():
    service = ScopedFsService(ops=None, commands=None)  # type: ignore[arg-type]

    with pytest.raises(ScopedFsPermissionDenied) as exc:
        await service.call(
            _ctx_with_tools("rw", frozenset({"fs_ls"})),
            "fs_write",
            {"path": "x.md", "content": "x"},
        )

    assert "disabled" in exc.value.message


@pytest.mark.asyncio
async def test_scoped_fs_grep_uses_shared_indexed_backend(monkeypatch):
    calls = {}

    def _indexed_payload(**kwargs):
        calls.update(kwargs)
        return {
            "index_status": "indexed",
            "index_freshness": {
                "indexed_commit_id": "head",
                "head_commit_id": "head",
                "commits_behind": 0,
            },
            "truncated": False,
            "hits": [{
                "path": "docs/a.md",
                "line": 3,
                "col": 5,
                "match": "hello mcp",
                "context_before": ["before"],
                "context_after": ["after"],
                "content_hash": "blob-hash",
            }],
        }

    class _Ops:
        def stat_in_scope(self, _project_id, _scope_path, _path, *, include_size=False):
            return None

        def get_scope_head_commit_id(self, _project_id, _scope_path):
            return "scope-head"

    monkeypatch.setattr(scoped_fs_service, "run_indexed_grep_payload", _indexed_payload)
    service = ScopedFsService(ops=_Ops(), commands=None)  # type: ignore[arg-type]

    result = await service.grep(_ctx("ro"), pattern="hello", path="")

    assert calls["scope_path"] == "docs"
    assert calls["excludes"] == ["private"]
    assert result["search_backend"] == "indexed"
    assert result["head_commit_id"] == "scope-head"
    assert result["matches"] == [{
        "path": "a.md",
        "line_number": 3,
        "line_text": "hello mcp",
        "match_text": "hello mcp",
        "match_start": 4,
        "match_end": 13,
        "byte_offset": None,
        "match_byte_offset": None,
        "before_context": [{"line_text": "before"}],
        "after_context": [{"line_text": "after"}],
        "content_hash": "blob-hash",
    }]


@pytest.mark.asyncio
async def test_agent_custom_search_is_dispatched_by_backend_runtime(monkeypatch):
    runtime = _runtime(_ctx("rw"), kind="agent")
    tool = SimpleNamespace(
        id="tool-1",
        name="search_docs",
        type="search",
        project_id="proj-1",
        path="docs",
        json_path="",
        input_schema=None,
        output_schema=None,
        description="Search docs",
    )
    monkeypatch.setattr(mcp_runtime, "resolve_mcp_runtime", lambda _key: runtime)
    monkeypatch.setattr(mcp_runtime, "_list_custom_tools", lambda _runtime: [({}, tool)])
    monkeypatch.setattr(
        mcp_runtime.SearchIndexTaskRepository,
        "get_by_tool_id",
        lambda _self, _tool_id: None,
    )
    search = SimpleNamespace(search_scope=AsyncMock(return_value={"results": []}))

    result = await mcp_runtime.call_runtime_tool(
        mcp_runtime.RuntimeCallRequest(
            api_key="mcp_key",
            name="tool_search_docs",
            arguments={"query": "hello", "top_k": 3},
        ),
        ops=None,
        commands=None,
        search_service=search,
    )

    assert result["structuredContent"] == {"results": []}
    search.search_scope.assert_awaited_once_with(
        project_id="proj-1",
        path="docs",
        tool_json_path="",
        query="hello",
        top_k=3,
    )


@pytest.mark.asyncio
async def test_custom_search_cannot_escape_or_enter_excluded_scope(monkeypatch):
    runtime = _runtime(_ctx("rw"), kind="agent")
    tool = SimpleNamespace(
        id="tool-1",
        name="search_private",
        type="search",
        project_id="proj-1",
        path="docs/private",
        json_path="",
    )
    monkeypatch.setattr(mcp_runtime, "resolve_mcp_runtime", lambda _key: runtime)
    monkeypatch.setattr(mcp_runtime, "_list_custom_tools", lambda _runtime: [({}, tool)])

    with pytest.raises(HTTPException) as exc:
        await mcp_runtime.call_runtime_tool(
            mcp_runtime.RuntimeCallRequest(
                api_key="mcp_key",
                name="tool_search_private",
                arguments={"query": "secret"},
            ),
            ops=None,
            commands=None,
            search_service=SimpleNamespace(),
        )
    assert exc.value.status_code == 403
