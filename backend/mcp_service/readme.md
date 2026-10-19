# PuppyOne MCP Service

Shared MCP protocol host for PuppyOne access endpoints.

## Final Architecture

```text
MCP Client
  -> Main Backend /api/v1/mcp/proxy
     -> authenticates Authorization: Bearer mcp_...
     -> proxies to internal mcp_service /mcp
        -> handles MCP protocol, sessions, streaming, tool validation
        -> calls Main Backend /internal/mcp-runtime/*
           -> resolves endpoint scope and permissions
           -> executes scoped_fs tools through Version Engine
```

Endpoint records live in `access_surfaces(kind='mcp')`. Runtime credentials
live in `access_surface_credentials` as HMAC hashes, and server-side tool/file
permissions live in `access_surface_policies`. Do not store MCP keys or policy
in `access_surfaces.config`.

`mcp_service` is a separate service by design. Do not embed the MCP protocol
server directly into the main FastAPI backend. The main backend owns public
auth, endpoint records, scopes, policy, and Version Engine execution. This
service owns MCP transport/protocol concerns only.

## Responsibilities

- Host the MCP Streamable HTTP endpoint at `/mcp`.
- Extract the runtime key from `X-API-KEY` after the main backend proxy.
- Load dynamic runtime config from the main backend internal API.
- List MCP tools from `/internal/mcp-runtime/tools` for standalone MCP endpoints.
- Execute MCP tools through `/internal/mcp-runtime/call`.
- Maintain MCP session state, event replay, cache invalidation, and health.

## Public Entry Point

External clients should connect to the main backend, not directly to this
service:

```json
{
  "mcpServers": {
    "puppyone": {
      "type": "http",
      "url": "https://api.example.com/api/v1/mcp/proxy",
      "headers": {
        "Authorization": "Bearer mcp_..."
      }
    }
  }
}
```

The public URL is stable for every MCP access point. The bearer key selects
the endpoint scope and server-side tool policy.

## Service Configuration

Main backend:

```bash
SERVICE_ROLE=api
MCP_SERVER_URL=https://your-mcp-service.up.railway.app
ALLOWED_HOSTS=https://your-frontend.example.com
INTERNAL_API_SECRET=...
```

MCP service:

```bash
SERVICE_ROLE=mcp_server
MAIN_SERVICE_URL=http://main-backend:9090
INTERNAL_API_SECRET=...
PORT=3090
```

`INTERNAL_API_SECRET` must match on both services.

## Start

```bash
uv run uvicorn mcp_service.server:app --host 0.0.0.0 --port 3090 --log-level info
```

For local compatibility:

```bash
python -m mcp_service
```

## Internal Endpoints

- `POST /mcp` - MCP protocol endpoint.
- `GET /healthz` - service health.
- `POST /cache/invalidate` - invalidate cached runtime config for an API key.

## Current Standalone MCP Tools

Standalone MCP endpoints expose scoped filesystem tools from
`src.platform.access.filesystem`:

- `fs_semantics`
- `fs_ls`
- `fs_tree`
- `fs_find`
- `fs_grep`
- `fs_cat`
- `fs_head`
- `fs_tail`
- `fs_stat`
- `fs_write`
- `fs_mkdir`
- `fs_touch`
- `fs_cp`
- `fs_mv`
- `fs_rmdir`
- `fs_rm`

Read-only endpoint scopes only list read tools. Writable endpoint scopes list
write/delete tools as well. Tool definitions include `title`, `description`,
`inputSchema`, `outputSchema`, and MCP `annotations`.

## Test Examples

Health:

```bash
curl http://localhost:3090/healthz
```

Tools list through the public proxy:

```bash
curl -X POST http://localhost:9090/api/v1/mcp/proxy \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mcp_..." \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

Tool call through the public proxy:

```bash
curl -X POST http://localhost:9090/api/v1/mcp/proxy \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mcp_..." \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "fs_ls",
      "arguments": { "path": "" }
    },
    "id": 2
  }'
```

## Adding MCP Filesystem Capabilities

Add filesystem tools in this order:

1. Define the tool contract in `src.platform.access.filesystem.registry`.
2. Implement behavior in `src.platform.access.filesystem.service`.
3. Keep MCP service as a thin protocol adapter.
4. Add registry, internal runtime, and MCP server routing tests.
