# ISSUE-022 — Web ⇄ Desktop cloud convergence

## Context / problem

The **web frontend** (`frontend/`, Next.js) and the **desktop cloud panel**
(the standalone `puppyone-desktop` repo, Electron/Vite) both re-implement the
same cloud functionality against the **same backend** (`/api/v1/*`): repo
scopes, connectors, MCP endpoints, sandbox endpoints, repo identity, the
access-provider registry, and the quick-connect flows. Every layer was
duplicated — types, API client, auth, data-fetching, UI — so a backend change
or a new provider had to be implemented twice and drifted.

They differ only in **how they authenticate + issue HTTP**:

- web → Supabase bearer token via `frontend/lib/apiClient`
- desktop → Electron IPC bridge (`window.puppyoneDesktop.requestCloudSessionApi`)

Everything above that transport (endpoint paths, request/response shapes, entity
types, pure domain logic) is identical.

## Architecture: shared core + injected transport

`@puppyone/cloud-core` is the single source of truth for the cloud domain. It is
strictly **platform-agnostic** — no Next.js, Electron, Supabase, or SWR imports
(enforced by `scripts/check-data-ui-boundaries.mjs`). Each platform provides a
`CloudTransport` (`get/post/put/patch/del`); the endpoint **factories**
(`createScopesApi`, `createConnectorsApi`, `createMcpEndpointsApi`,
`createSandboxEndpointsApi`) build the domain functions on top of it.

- **Web** binds via `frontend/lib/cloudCoreTransport.ts` (a straight pass-through
  to `apiClient`, whose exports already match `CloudTransport`). The old
  `lib/repoApi.ts`, `mcpEndpointsApi.ts`, `sandboxEndpointsApi.ts`,
  `accessProviderRegistry.ts` are now thin bindings / re-exports — **zero
  call-site churn**, single source of truth.
- **Desktop** (Phase 2) will bind the same factories to an IPC transport.

Sharing mechanism (decided): `frontend/packages/cloud-core` lives inside the
web deployment context and is consumed as a frontend-local npm dependency; the standalone
`puppyone-desktop` will consume it as a **git submodule** (or build artifact).

## Status

### ✅ Phase 1 — shared cloud-core + web consumption (DONE, verified)

- `frontend/packages/cloud-core`: `transport.ts`, `accessProviders.ts`, and
  `endpoints/{scopes,connectors,mcpEndpoints,sandboxEndpoints}.ts` covering the
  core duplicated entities + pure helpers (`matchScopeForPath`, `isWithinScope`,
  `sortConnectorsBuiltinFirst`, connector normalization, …).
- Web frontend consumes it; `repoApi/mcpEndpointsApi/sandboxEndpointsApi/
  accessProviderRegistry` are now bindings/re-exports.
- Verified: `cd frontend && tsc --noEmit` → **0 errors** (all ~59 call sites of
  repoApi + ~27 of the registry still resolve); boundary check passes.

### ⬜ Phase 2 — desktop consumes cloud-core (next)

- Add `cloud-core` to `puppyone-desktop` as a git submodule; alias it in
  `vite.config.ts` + `tsconfig.json` (same pattern the monorepo desktop uses for
  `@puppyone/data-*`).
- Write a desktop `CloudTransport` over the IPC bridge; refactor
  `desktop/src/lib/cloudApi.ts` to consume the cloud-core factories, deleting the
  duplicate type + endpoint definitions there.
- Verify: desktop `npm run build` (tsc + vite).

### ⬜ Phase 3 — remaining API clients + shared hooks

- Migrate the other overlapping clients (`syncApi`, `scopeSyncApi`,
  `scopeSandboxApi`, `projectsApi` cloud subset) into cloud-core.
- Extract the shared data-fetching **logic** (selection/bucketing/pause-resume)
  so the web `useAccessData` (SWR) and desktop `useDesktopCloudData` (custom
  hooks) share a framework-agnostic core, with thin per-platform adapters.

### ⬜ Phase 4 — shared cloud UI (largest)

- Converge the presentational components (Access section, scope detail, connect
  methods) behind a shared `cloud-ui` package with injected routing/navigation,
  once Phases 1–3 have stabilized the data layer. This is the biggest and last
  step; defer until the shared data core is proven in both apps.

## Non-goals / guardrails

- No behavior change in Phase 1 — pure de-duplication behind identical exports.
- cloud-core must never import a platform runtime (CI boundary check enforces).
- Each phase is independently shippable and leaves no half-migrated duplication.
