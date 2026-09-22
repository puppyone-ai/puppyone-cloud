"""
Connectors Module — All connection types for PuppyOne.

Seven peer-level areas, backed by connectors bound to repo scopes:

  connectors/
  ├── manager/            Unified connection CRUD (single entry-point)
  ├── datasource/         SaaS data sources (Gmail, Notion, GitHub, ...)
  │   └── oauth/          OAuth authorization flows & token storage
  ├── database/           External database connectors
  ├── agent/              AI agents (config, chat, MCP tool binding)
  ├── mcp_endpoint/       MCP protocol endpoint CRUD & API key
  └── sandbox_endpoint/   Sandbox endpoint CRUD & command execution

All sandbox providers and execution modes live in src/platform/scope_sandbox/.
MCP health and cache transport boundaries live in src/infra/mcp_health.py and
src/connectors/mcp_cache.py; endpoint/runtime logic uses scoped_fs.
Workspace materialization (lower cache) lives in src/platform/workspace/.
"""
