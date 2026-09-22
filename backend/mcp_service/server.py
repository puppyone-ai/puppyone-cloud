"""
Shared MCP protocol service.

The main backend owns public authentication, endpoint/scope resolution, and
Version Engine execution. This service owns MCP transport, sessions, tool
listing/calling, and protocol-level validation.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import logging
from typing import Any, AsyncIterator

from mcp.server.lowlevel import Server as MCP_Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import mcp.types as mcp_types

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from .core.auth import extract_api_key
from .core.session_registry import SessionRegistry
from .event_store import InMemoryEventStore
from .rpc.client import create_client

logger = logging.getLogger("mcp_service.server")

def _runtime_tool_to_mcp(tool: dict[str, Any]) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=tool.get("name", ""),
        title=tool.get("title"),
        description=tool.get("description") or tool.get("title") or tool.get("name", ""),
        inputSchema=tool.get("inputSchema") or {"type": "object", "additionalProperties": False},
        outputSchema=tool.get("outputSchema"),
        annotations=tool.get("annotations"),
    )


def build_starlette_app(*, json_response: bool = True) -> Starlette:
    """Build a Starlette application instance (MCP handler assembly)."""

    # 1. Create StreamableHTTPSessionManager
    ## MCP server
    mcp_server = MCP_Server("puppyone-contextbase-mcp")
    ## Session registry: used to notify active clients of tool changes
    sessions = SessionRegistry()
    ## Event store implementation: used for SSE event replay
    event_store = InMemoryEventStore()
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=event_store,
        json_response=json_response,
        stateless=False,
    )

    # 2. Create internal RPC client
    rpc_client = create_client()

    ####################
    ### Protocol interface hooks
    ####################

    @mcp_server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        """List available tools for the resolved MCP runtime."""
        try:
            ctx = mcp_server.request_context
            request = ctx.request
            if request is None:
                return []

            # 1. Extract api_key
            api_key = extract_api_key(request)
            # 2. Bind api_key and session for subsequent notifications
            await sessions.bind(api_key, ctx.session)

            runtime = await rpc_client.list_mcp_runtime_tools(api_key)
            surface_id = str((runtime.get("endpoint") or {}).get("id") or "")
            if surface_id:
                await sessions.bind_surface(surface_id, ctx.session)
            return [_runtime_tool_to_mcp(tool) for tool in runtime.get("tools", [])]
        except Exception as exc:
            # A config/RPC failure must NOT collapse to [] — that is identical to
            # "this endpoint has no tools" from the client's view. Log it and
            # surface a protocol error instead (mirrors call_tool's behavior).
            logger.exception("MCP list_tools failed")
            raise RuntimeError(f"Failed to list MCP tools: {exc}") from exc

    @mcp_server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | list[mcp_types.TextContent] | mcp_types.CallToolResult:
        """Execute a tool call for the resolved MCP runtime."""
        try:
            ctx = mcp_server.request_context
            request = ctx.request
            if request is None:
                raise RuntimeError("missing request context")

            api_key = extract_api_key(request)
            await sessions.bind(api_key, ctx.session)

            result = await rpc_client.call_mcp_runtime_tool(
                api_key, name, arguments or {}
            )
            if result.get("isError"):
                err = result.get("error") or {
                    "message": "tool call failed",
                    "status_code": result.get("status_code"),
                }
                return mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(
                        type="text",
                        text=json.dumps(err, ensure_ascii=False, indent=2),
                    )],
                    isError=True,
                )
            structured = result.get("structuredContent", result)
            if isinstance(structured, dict):
                return structured
            return {"result": structured}
        except Exception as e:
            # Log the full traceback server-side; return a concise message to the
            # external client (don't leak internal stack traces over the wire).
            logger.exception("MCP call_tool failed: tool=%s", name)
            return [mcp_types.TextContent(type="text", text=f"Error: {e!s}")]

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope=scope, receive=receive, send=send)

    async def handle_healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "mcp-service"})

    async def handle_cache_invalidate(request: Request) -> JSONResponse:
        """
        Main service notifies this MCP server that instance data has changed;
        the corresponding cache entries must be invalidated.

        Authenticated with the shared internal secret (ISSUE-008): this endpoint
        is a server-to-server hook, never client-facing. Without auth, anyone
        reachable could probe cache state and trigger cache-stampede DoS.
        """
        from .settings import settings as mcp_settings

        expected_secret = (mcp_settings.INTERNAL_API_SECRET or "").strip()
        provided_secret = request.headers.get("x-internal-secret", "")
        if not expected_secret or not hmac.compare_digest(provided_secret, expected_secret):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
            api_key = body.get("api_key")
            access_surface_id = body.get("access_surface_id")
            table_id = body.get("table_id")

            if api_key:
                notified = await sessions.broadcast_tools_list_changed(api_key)
                return JSONResponse(
                    {"message": "Notified credential sessions", "notified_sessions": notified}
                )

            if access_surface_id:
                notified = await sessions.broadcast_surface_changed(str(access_surface_id))
                return JSONResponse(
                    {
                        "message": f"Notified sessions for access_surface_id={access_surface_id}",
                        "notified_sessions": notified,
                    }
                )

            if table_id:
                return JSONResponse({
                    "message": f"No transport data cache exists for table_id={table_id}"
                })

            return JSONResponse(
                {"error": "Missing api_key, access_surface_id, or table_id parameter"},
                status_code=400,
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        from .settings import settings as mcp_settings

        await sessions.start(mcp_settings.REDIS_URL)
        async with session_manager.run():
            yield
            await sessions.close()
            await rpc_client.close()

    app = Starlette(
        routes=[
            Mount("/mcp", app=handle_mcp),
            Route("/healthz", handle_healthz, methods=["GET"]),
            Route("/cache/invalidate", handle_cache_invalidate, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    # CORS (ISSUE-008): never pair wildcard origins with credentials. MCP auth
    # is header-based (Authorization / api-key), not cookie-based, so credentials
    # are disabled and origins default to a config-driven allowlist.
    from .settings import settings as mcp_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=mcp_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app

def load_settings():
    """Load and validate settings (raises on failure so uvicorn surfaces the error at startup)."""
    from .settings import settings

    try:
        settings.validate()
    except ValueError as e:
        logger.error("MCP server configuration error: %s", e)
        raise

    # Display settings (sensitive values are masked)
    print("MCP Server settings:")
    for key, value in settings.display().items():
        print(f"  {key}: {value}")
    print()

    return settings


def create_app() -> Starlette:
    """Create a Starlette application instance (same style as main service `src/main.py`: exports `app`)."""
    settings = load_settings()
    app = build_starlette_app()
    print(
        f"""
╔══════════════════════════════════════════════════════════╗
║  ContextBase MCP Server - Shared service mode           ║
╠══════════════════════════════════════════════════════════╣
║  Listen:   {settings.HOST}:{settings.PORT}                              ║
║  MCP:      http://{settings.HOST}:{settings.PORT}/mcp                   ║
║  Health:   http://{settings.HOST}:{settings.PORT}/healthz              ║
║  Cache:    {settings.CACHE_BACKEND} (TTL: {settings.CACHE_TTL}s)                    ║
╚══════════════════════════════════════════════════════════╝
"""
    )
    return app


# uvicorn start command (recommended to use uv run, consistent with main service):
# uv run uvicorn mcp_service.server:app --host 0.0.0.0 --port 3090 --reload --log-level info
app = create_app()
