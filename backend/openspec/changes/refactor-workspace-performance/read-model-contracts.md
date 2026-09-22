# Read-model contracts — migration review required

Status: design only, reviewed against the current read/authorization code on
2026-09-19. No SQL migration, RPC deployment or production query-plan claim is
included in this implementation. The additive `/content/{project_id}/head` and
bounded commit previews work with the existing schema.

## Visible linear history

Current source: `SupabaseHistoryManager.get_since`,
`VersionAdminService._get_commit_history_sync`, `_is_user_visible_history_entry`.
The canonical records are Git commits; the history table is a projection. Do not
turn a new pagination index into a second publication authority.

The current implementation still scans `max(limit * 20, 1000)` raw rows to hide
legacy scope-promote projections. Wire pages are now capped (latest tail without
an anchor, earliest next page with an anchor). This bounds payload and parent
hydration, **not** the database scan or the completeness of an arbitrarily long
run of invisible rows. Do not simply reduce the multiplier or infer exhaustion
from a filtered short page when redesigning this contract.

Proposed repository interface (not a new public URL yet):

- Inputs: project identity from an authorized request; normalized optional path;
  page size `1..100`; direction; exclusive signed cursor; optional snapshot fence.
- Cursor includes project, filter digest, direction, `(created_at, commit_id)`
  position and schema version. Include the last **scanned** position, not just the
  last visible row. A cursor cannot be reused for another project/filter/order.
- Result: visible entries, next cursor, `has_more`, rows-scanned counter, and an
  explicit scan-budget-exhausted signal. An empty visible page with progress is
  not end-of-history. A bounded scan may continue on the next request.
- Catch-up must define an explicit beginning cursor for an initially empty
  repository. Missing/pruned anchors must yield a resynchronization signal;
  an unknown anchor returning `[]` cannot prove that nothing changed.
- Preserve stable timestamp ties using commit ID; retain exact path matching and
  the current message/change-action visibility rules, including legacy JSON text.
- An index/generated visibility field is derived, rebuildable, and backfilled in
  a separate reviewed migration. No wall-clock authorization cache is introduced.

Acceptance before deployment: compare the old and new visible sequences on
fixtures containing legacy projections, >1000 consecutive hidden rows, missing
anchors, equal timestamps, file paths, empty repositories and concurrent writes.
Collect `EXPLAIN (ANALYZE, BUFFERS)` on staging-scale cardinalities for recent,
anchored and path-filtered reads; report rows scanned, p50/p95, cold/warm object
GETs and parent-batch counts. Rollback restores the adapter, not Git history.

## Authorized workspace read snapshot

Current facts: AuthorizationService loads project, org membership and project
membership, then evaluates named ProjectAction. Root/head are resolved through
the version read layer. Request-local authorization caching remains valid;
cross-request grant caching is not an acceptable latency shortcut.

Proposed read model:

1. A repository-level join/RPC loads the required authorization **facts** and a
   coherent canonical root/head pair in one database snapshot. It returns an
   internal typed record, not an HTTP response or a grant.
2. The existing canonical PDP evaluates the requested ProjectAction before any
   content/root/head is disclosed. Do not duplicate the role policy inside a
   second SQL authorization engine or trust a browser-supplied org/role.
3. The authorized snapshot carries project/root/head identity and the facts used
   in this request. Blob/tree traversal uses immutable OIDs from that snapshot;
   stat and cat must not independently choose different mutable roots.
4. A root/head mismatch during legacy repair is explicit, not silently treated
   as a coherent snapshot. Keep existing repair/fallback policy under the version
   owner. Never let a cached ref or connector credential authorize the read.
5. RPC role grants/search_path, RLS behavior and service-role access are reviewed
   explicitly. Permission denial/membership revocation/project deletion tests
   precede activation; logs record timing/IDs, never credentials or file bodies.

Schema-dependent rollout is a separate change: migration + compatibility phase,
staging query plans, authorization matrix, concurrent publication/revocation
tests, opt-in adapter, and measured rollback criteria. No production database was
changed as part of this refactor.
