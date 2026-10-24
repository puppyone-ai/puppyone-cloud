# Retired internal node HTTP endpoints

Status: History. Archived 2026-10-24 from `puppyone-cloud@51d5b53a` by explicit
user decision. The complete `/internal/nodes/*` surface is removed, including
resolve-path, list, read, write, create, rm, rename and move.

`router.py.archived` preserves the old handlers and write-command helper for
reference. The files deliberately have a non-Python extension, are outside
`src`, and must never be imported, mounted or used as a runtime fallback.

The obsolete POSIX unit suite and legacy MCP E2E suite are preserved with the
same non-collectable extension. The latter builds its entire fixture through
retired node endpoints and old Agent MCP setup, so it no longer describes the
current MCP runtime. Current MCP runtime/transport tests stay under `tests/`.
Active retirement tests prove all old routes absent from the application and
OpenAPI, even with valid internal credentials.

Other internal APIs (billing, table, search and `/internal/mcp-runtime/*`) are
not retired by this change. Native workspace/Fly and published Git file paths
retain their existing owners and authorization requirements.
