"""Internal MCP tool discovery and dispatch using shared Access filesystem helpers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.internal.dependencies import verify_internal_secret
from src.version_engine.bootstrap.dependencies import (
    get_product_operation_adapter,
    get_version_write_command_service,
)
from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.access.filesystem.errors import ScopedFsError
from src.platform.access.filesystem.registry import (
    MCP_FS_TOOL_NAMES,
    build_mcp_tool_definitions,
)
from src.platform.access.adapters.mcp_endpoint.resolver import resolve_mcp_runtime
from src.platform.access.filesystem.service import ScopedFsService


router = APIRouter(
    prefix="/internal/mcp-runtime",
    tags=["internal-mcp-runtime"],
    dependencies=[Depends(verify_internal_secret)],
)


class RuntimeToolsRequest(BaseModel):
    api_key: str = Field(..., min_length=1)


class RuntimeCallRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/tools")
def list_runtime_tools(payload: RuntimeToolsRequest):
    runtime = resolve_mcp_runtime(payload.api_key)
    ctx = runtime.context
    tools = build_mcp_tool_definitions(
        writable=ctx.writable,
        allowed_tools=ctx.allowed_tools,
    )
    return {
        "mode": runtime.surface_kind,
        "endpoint": {
            "id": ctx.endpoint_id,
            "name": ctx.endpoint_name,
            "project_id": ctx.project_id,
            "scope_id": ctx.scope_id,
            "scope_path": ctx.scope_path,
            "mode": ctx.mode,
        },
        "tools": tools,
    }


@router.post("/call")
async def call_runtime_tool(
    payload: RuntimeCallRequest,
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    runtime = resolve_mcp_runtime(payload.api_key)
    ctx = runtime.context
    scoped_fs = ScopedFsService(ops, commands)
    try:
        # Only the filesystem tools are served: Search Tools were retired with
        # the vector search backend and no other custom tool type executes.
        if payload.name not in MCP_FS_TOOL_NAMES:
            raise ScopedFsError("UNKNOWN_TOOL", f"Unknown MCP tool: {payload.name}")
        result = await scoped_fs.call(ctx, payload.name, payload.arguments)
    except ScopedFsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return {
        "tool": payload.name,
        "structuredContent": result,
        "isError": False,
    }
