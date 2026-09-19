# Workspace performance design

## Context

See `docs/architecture/archive/2026-09-19-web-performance-audit.md` at repository
root. Existing Next 15, React 18, SWR, Zustand and FastAPI boundaries remain.

## Goals / Non-Goals

- Immediate, truthful navigation feedback; project-isolated state restoration.
- Bounded foreground work and a single owner for each category of state.
- No event-loop blocking by synchronous database/storage calls on audited reads.
- Preserve access revocation, root-first writes, replay ordering and draft safety.
- Not a microservice split, router rewrite, visual redesign or blanket file move.
- Database RPC/index changes require a separate migration review with query plans;
  do not fabricate a measured SQL bottleneck or ship a schema dependency silently.

## Decisions

### Frontend directories and dependencies

`app/` owns routes, layout and loading/error boundaries, not reusable business
logic. `features/workspace/` owns navigation/session lifecycle; `features/files/`
owns file selection/editor integration; `features/history/` owns diff/read UI;
`features/access/` owns access views. Extract touched logic incrementally rather
than mechanically moving all legacy files. `components/` remains domain-neutral
UI and the existing reusable editor modules. `lib/` owns transport, pure helpers
and existing API clients. Features must not import route entry files. Tests live
next to feature logic or under an explicitly scoped test tree.

URL owns shareable location. SWR owns remote facts. A project-scoped selector
store owns view preferences/session state, not copies of SWR payloads. Drafts
belong to editor sessions and keep persistence/guard guarantees. Context supplies
stable services/actions and low-frequency identity, not every editing event.

### Workspace lifecycle

Share header, navigation, and auxiliary-sidebar ownership at the project
boundary. Files and Git remain primary content views and contribute only their
breadcrumbs and page-specific actions into stable header slots; Access and Chat
are right-aligned header utilities rendered in one persistent, resizable sidebar.
Preserve per-view selection and last file; load inactive heavy content on demand. Keep browser
back/forward and deep links valid. Switching projects must never reuse another
project's selection, draft or credentials. Dirty editor navigation must follow a
single guard rather than bypassing save semantics via a header link.

### Queries, cancellation and events

Centralize keys for resources shared by multiple views. Distinguish mutable HEAD
from immutable commit/OID content. Deduplicate pending conflicts before selecting
conflict/review subsets. Coordinate invalidation at project scope; do not disable
revalidation without replacing its freshness guarantee. Use cancellation plus
deadlines for reads, preserving independently tracked writes. Avoid logging
signed URLs or tokens when reporting failed requests.

### Staged workspace reads (2026-09-19 follow-up)

Navigation is a metadata/grant list, not a tree scan. Existing list URLs accept
`include_access_counts=false`; the Web navigation adapter uses this projection
and keeps the default counted response for older clients. Unrequested counts
are null rather than false zeroes. Canonical batch authorization is unchanged.

Auth and organization discovery remain explicit dependencies, with user-scoped
organization preferences and no effect-only selection gap. A deep-linked
project may supply its authorized org before the org list arrives. The route
detail never becomes a synthetic one-item navigation list, and file readiness
does not depend on the rest of the navigation list. Member directories are
requested by member-management consumers, not by mounting the shell.

`lib/queryState` distinguishes idle, loading, ready and error. Only a successful
snapshot may be interpreted as empty. Same-key revalidation preserves cached
content; different org/project/path keys must not retain previous data. Failed
reads show retry rather than misleading empty content. Failed text loads cannot
be saved, and do not initiate a duplicate speculative read.

`features/files/useFileWorkspaceQueries` owns the read lanes: the active root
first (shared with the explorer), then seven auxiliary metadata/tool queries.
Subdirectories load on expansion/path navigation, with expansion keyed by both
project and path. No timer-based delay or all-project recursive preload is added.
This is incremental orchestration over existing authorized APIs, not a new
monolithic bootstrap endpoint or a replacement data store.

Remaining limits: existing project-list pagination semantics (including the
repository's 100-row default), global SWR auth-session isolation beyond the org
keys, Agent/activity eager work, and browser p95 measurements require follow-up.
Do not describe this slice as complete elimination of all startup requests.

### Backend execution details

Keep canonical AuthorizationService decisions before reads. Use bounded worker
offload for complete synchronous read units in async services; sync routes may
use FastAPI's worker execution. Reuse a shared limiter rather than spawning an
unbounded thread per database operation. Propagate request context into workers.

Head-only synchronization must not load a history page. Visible history needs
bounded paging that preserves filters and exclusive catch-up anchors. Parent
object fetches reuse immutable object caching/batching, not a second source of
truth. Read snapshot aggregation must retain canonical root/head consistency and
requires explicit DB contract design, not ad hoc authorization caching.

### Diff and editor work

Extract a pure diff model; give it byte/line/work/output budgets, cache immutable
results and keep computation out of React render. Worker execution must have a
bounded fallback and error handling. Large changes show an explicit truncated
preview rather than allocating a DOM node per source line. Draft persistence
changes must preserve flush-on-navigation/pagehide and save/error recovery.

## Risks / Trade-offs

- Keeping every view mounted trades latency for memory: retain session state,
  not all expensive component trees.
- Cancelling a coroutine cannot stop an already-running sync operation: bound
  concurrency and configure I/O timeouts; never promise instant DB cancellation.
- Lowering history limits can lose reconnect events: validate ordering, filtering,
  cursor progress and complete replay before replacing the current query.
- Source-level checks cannot prove UI behavior: use actual React runtime tests
  and production-mode browser measurements separately.

## Migration / Rollback

Fix bugs and add regression tests first. Extract narrow modules with stable
exports, then migrate call sites. Additive interfaces precede client adoption.
Keep schema/deployment changes separate. Rollback a slice by restoring its
adapter/call site; do not revert unrelated in-progress visual changes.
