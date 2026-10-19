# Change: Finish Git, MCP and repository Scope ownership

## Why

The user approved the final two structure batches before overall acceptance.
Git transport still lives inside Version Engine; MCP dispatch, shared CLI/MCP
filesystem helpers, Scope geometry and credential resolution have mixed owners.

## What Changes

- Move Git routes, authentication and smart-HTTP transport into Access's Git
  adapter. Keep shared cache/locking and object-format facts in Version Engine;
  move run-bound publication transport into the existing Agent control module.
- Move MCP runtime routes and credential resolution to the MCP adapter. Keep
  the existing CLI/MCP filesystem helpers together under access/filesystem;
  this is their existing shared implementation, not a new filesystem backend.
- Move Scope CRUD and models into repository_target, and project connection
  identity into Access. Move legacy Scope credential resolution into Access,
  separate from geometry persistence. Remove unused legacy permission DTOs.
- Update callers, source-boundary guards and architecture documentation; remove
  obsolete modules without compatibility copies.

## Impact

- Affected spec: project-structure. Preserve URLs, OpenAPI, credential types,
  fail-closed Scope behavior, tool definitions, Git publication and I/O budgets.
- No database migration, frontend/provider change, hosted mutation or deployment.
  Native Scope/MCP enablement and Table/share retirement remain separate work.
