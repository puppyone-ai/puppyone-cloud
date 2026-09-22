# PUP — GitHub import conflict semantics (2026-06)

Status: **IMPLEMENTED** (2026-06-21). Gate lives in `importer._do_import` (source_channel-aware
divergence check over `version_transactions`, force-overridable, fail-open). Live-verify against a
real integration with divergent writes still recommended. Original design below.
Owner area: `backend/src/repo/github_integration/`, `version_engine` history
Related: `docs/architecture/01-version-engine.md` §"并发 push / 冲突", GAP-3 (branches), `importer.py` `ImportConflict`

## Problem

GitHub import overwrites the bound scope unconditionally. `import_branch(force=…)`
accepts a `force` flag but it is **never read**; the conflict gate in
`importer.py` (`_do_import`) is a documented no-op. So a re-import (manual or
**webhook**) silently discards any local edits made to the imported content
through other entry points (Web editor / PAPI / CLI / another connector). For a
bidirectional import+export workflow this is silent data loss.

A naive fix — "refuse unless `force` when the current scope head ≠ the last
import's commit" — is **wrong**: scope-sync **projection** advances the scope head
on its own (commits with `source_channel="scope-sync"`), so that check would make
nearly every re-import a false conflict and break webhooks.

## Design principle (per product direction)

Align with the version engine's existing per-scope conflict model — the
**server-side scope head is source of truth**, and "conflict" is judged the way
the Git transport judges it, **channel-aware**:

- A **Git push** conflicts when the scope head moved off the client's base →
  `non-fast-forward` (client rebases). (`01-version-engine.md` cases 一/七)
- A **non-Git write** (PAPI/CLI/**connector** — GitHub import is connector-style)
  bases its CAS on "the scope head read at intent time"; a plain CAS race is a
  **transparent server retry**, and only a **true same-file divergence** between
  two *entries* becomes a hosted `pending review`. System maintenance
  (scope-sync projection) is **not** a conflicting entry. (cases 六/七)

GitHub import is special in that it **overwrites** the whole scope tree (it is not
a graft), so the relevant question is exactly the Git "did the scope diverge"
question, restricted to *real* writers.

## Semantics (the rule)

A GitHub import into bound scope `S` is allowed to overwrite **iff** no
**external/user** write landed on `S` since this integration's last successful
import. Concretely:

1. `last = ` version_commit_id of this integration's most recent **successful**
   import (from `github_sync_log`).
2. If `last is None` (first import) or the scope has no head → **proceed**
   (first-push / INSERT case 九).
3. Otherwise compute `diverged = history.get_since(last, scope_path=S)` filtered
   to entries whose `source_channel ∉ OWN_OR_SYSTEM`.
4. `diverged` non-empty → **conflict**: raise `ImportConflict(current_head, last)`
   unless `force=True`. The existing handler records `status="conflict"` in
   `github_sync_log` and returns it (mirrors a Git `ng` / non-fast-forward).
5. `force=True` → overwrite anyway (mirrors opt-in force push, case 八).
6. `diverged` empty → **proceed** (only this integration + system projection
   touched `S`; the import is the intended fast-forward update).

Idempotent re-delivery of the same git sha is already short-circuited
(`has_successful_sha`) before this gate, so a no-op re-import never conflicts.

## Channel taxonomy (CONFIRMED against the codebase)

Enumerated from `source_channel="…"` across `backend/src`:

| source_channel | class | rationale |
|---|---|---|
| `github` | OWN | this integration's own prior imports — not a conflict |
| `scope-sync` | SYSTEM | projection re-derivation, not a user edit |
| `access_git` | EXTERNAL → conflict | a user pushed local commits |
| `access_cli` | EXTERNAL → conflict | user edited via the CLI |
| `mcp` | EXTERNAL → conflict | external AI tool edited content |
| `papi` | EXTERNAL → conflict | Web editor / product API edit |
| `access_sandbox` | EXTERNAL → conflict | edit from a sandbox session |
| `upload` | EXTERNAL → conflict | a file upload landed here |
| `sync` | EXTERNAL → conflict | a durable connector wrote here |
| `import` | EXTERNAL → conflict | a different one-shot import wrote here |

`OWN_OR_SYSTEM = {"github", "scope-sync"}`; everything else is external
divergence. New channels default to EXTERNAL (fail-closed: an unknown writer is
treated as a real edit, so the worst case is a recoverable false conflict the
user clears with `force`, never silent data loss).

## Implementation sketch

**Data-source note (verified):** `source_channel` is **not** in the commit-history
table that `history_repository.get_since` reads (it stores `who`/`message`/
`scope_path` only). It lives on **`version_transactions`** (written by
`transaction_ledger.insert_version_transaction(scope_path, source_channel, …)`).
The ledger today only exposes *writes* + pending-conflict CRUD — there is **no**
"list transactions since X for scope" read. So the gate needs:

1. A new ledger read, e.g. `list_scope_transactions_since(scope_path, since_ts)
   -> [{source_channel, created_at, …}]` (or a `commit_id` anchor if the ledger
   stores it). No schema change — `version_transactions` already has the columns.
2. **Coverage check (blocking):** confirm EVERY external write path
   (`access_git`/`access_cli`/`mcp`/`papi`/`access_sandbox`/`upload`/`sync`/
   `import`) actually inserts a `version_transaction`. If any path skips it, the
   gate would miss that divergence — verify before relying on it.
3. `GithubSyncLogRepository.latest_successful_import_commit(integration_id)` (and
   its timestamp) — anchor for "since"; `version_commit_id` is already recorded,
   no migration.
4. Thread `force` into `_do_import`; before `_make_overwrite_splice`, run steps
   1–6 against the new ledger read filtered to `source_channel ∉ OWN_OR_SYSTEM`.
5. Edge — no/!anchorable `last`: a non-None-but-unresolvable anchor → treat as
   conflict (fail-closed; `force` overrides) so a lost anchor never silently
   overwrites. First import (`last is None`) → proceed.
6. Webhook keeps `force=False`, so a webhook into a diverged scope records a
   `conflict` sync-log row (surfaced in the UI) instead of clobbering.

## Testing plan

- Pure classification: external channel → conflict; only github+scope-sync →
  no conflict; empty `last` → no conflict; `force=True` bypasses.
- `get_since` interaction with a fake ledger (diverged vs clean).
- Webhook path: diverged scope → `status=conflict` recorded, splice not applied.

## Why this is a proposal, not yet a commit

The gate runs on every webhook import; shipping it wrong either re-introduces
silent overwrite or breaks all webhook imports with false conflicts. Settled now:
the **semantics** (above) and the **channel taxonomy** (verified against the
codebase). Remaining before a safe commit:

1. Add the `version_transactions` since-by-scope read (no schema change).
2. **Verify every external write path records a `version_transaction`** — this is
   the one load-bearing unknown; a write path that skips the ledger would make
   the gate miss real divergence.

After (1)+(2) the implementation is small (importer gate + sync-log query +
ledger read + tests), no migration. Recommend doing it as a focused pass rather
than at the tail of a large change, given it gates webhooks.
