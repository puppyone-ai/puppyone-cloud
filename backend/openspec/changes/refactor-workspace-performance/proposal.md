# Change: Refactor workspace state and read-path performance

## Why

The 2026-09-19 performance audit identified a render/effect feedback loop,
blocking synchronous I/O in async routes, excessive history reads, remounted
workspace state, duplicate queries and unbounded diff rendering. The user has
requested systematic frontend/backend refactoring with regression coverage.

## What Changes

- Restore convergent render behavior and non-blocking read execution first.
- Define frontend boundaries: App Router composition, domain features,
  server-state queries, project-scoped UI sessions, editor sessions, and shared UI.
- Introduce project-scoped workspace state and shared query ownership without
  changing public URLs, authorization policy, or canonical Git write authority.
- Separate lightweight head synchronization from paginated history; preserve
  linear catch-up and topological-history semantics.
- Bound diff work/rendering, deduplicate shared resources, and standardize
  request cancellation/deadlines and request correlation.
- Add real React regression tests, ASGI concurrency tests, boundary checks,
  and documented acceptance scenarios.

## Approval

Status: approved by the user on 2026-09-19 ("确认，按此方案实施").
Implementation is authorized within this design, excluding production database
changes, deployment and Git publication.

## Impact

- Affected specs: `workspace-performance` (new cross-layer contract).
- Frontend: project routes, features, contexts, queries, HTTP client/BFF, tests.
- Backend: activity, content history/read services, authorization read boundary.
- Existing pending `refactor-context-entrypoints` remains authoritative for
  Upload/Import/Connect/Access lifecycle; this proposal does not replace it.
- No production migration, deployment, Git publication, or dependency-framework
  replacement is authorized by this implementation plan.
