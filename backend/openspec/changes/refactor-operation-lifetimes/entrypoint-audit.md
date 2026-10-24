# Operation entrypoint coverage audit

Status: Evidence / Known gaps. Audited 2026-10-24 against local `qubits` baseline
`c35bca67`, including its 17 existing modified files. This is an adoption audit,
not a declaration that every backend operation is migrated.

The canonical design remains in
`puppy-issues:document/puppyone/architecture/06-data-access-contracts.md`.

## Findings and priority

Counts below describe explicit application calls in the named path, excluding
transactional authority checks, leases, retries and storage internals. They are
not measured network request counts or seconds. N means the number of files
actually processed, not the number of AST call sites.

| Priority / entry | Concrete owner | Observed behavior | Required closure |
| --- | --- | --- | --- |
| Fixed / public Synchronize push | `platform/synchronize/public_router.py:push_synchronize_path`, `router.py:trigger_push` | Public wrapper omitted `ops`; Python left a `Depends` object and the request failed. Content and head also came from separate snapshots. | Wrapper now forwards injected ops; one off-loop authorization produces the bound grant; content and head share one `run_read`. |
| P1 / upload postprocessing | `platform/upload/jobs.py:etl_postprocess_job` | JSON mount path has separate stat/content snapshots. Any content-read exception becomes `{}` before a later write. A storage outage is therefore indistinguishable from empty input. | Preserve missing versus unavailable/denied/corrupt errors; capture content and base revision together; pass that revision into checked publication. Test outage and concurrent edit before optimizing call counts. |
| P1 / dashboard | `platform/project/resource_dashboard.py:get_resource_dashboard` → `dashboard_router.py:_compute_node_counts` | Current mounted caller does not bind `authorized.grant`. Head error is swallowed; tree read then fails. Even with a bound adapter, head and tree use different snapshots and can cache counts under the wrong head. | Bind existing grant, capture head/tree in one session, test concurrent ref updates and cache isolation. |
| P1 / Agent metadata | `platform/access/adapters/agent/config/router.py:list_agents` | Dependency authorizes Agent read but returns only project ID. Loop calls unbound `ops.stat`, catches the failure and omits file metadata. If merely bound, it would still open N snapshots. | Carry the authorized grant; one session for all distinct paths; test missing path separately from denied/unavailable. |
| P1 / repository identity | `platform/access/identity_router.py:get_access_point` | Mounted route has `AuthorizedProject` but reads head with unbound ops. | Bind existing grant; one bounded head read. Test the actual route with the real adapter boundary. |
| P1 / internal file HTTP | `internal/router.py:resolve_node_path`, `list_node_children`, `read_node_content` | Internal secret + acting-user check is legitimate. The subsequent `for_user` repeats Project authorization. Read-content has two snapshot lifetimes (stat + content/list); synchronous auth/storage run on the event loop. | Keep service credential and acting-user identity; return/reuse the ProjectGrant, then one owned read. Root/list/read and denial tests must use mounted routes. |
| P1 / search indexing | `infra/search/service.py:_index_folder_admitted`, `_index_file_node`, `_index_scope_admitted` | Folder chain explicitly performs N+2 authorization evaluations and N+1 read sessions. `for_user` runs before `to_thread`; per-file body reads are synchronous inside async code. Listing/content can describe different versions. | One explicit job read scope with bounded processing, cancellation and pin renewal; no whole-repository RAM materialization. Retain durable-job admission/revalidation. |
| P2 / local workspace materialization | `platform/workspace/sync_worker.py:sync_project`, called by `local_service.py` | Injected path is grant-bound, but head/tree/N files use N+2 one-shot sessions. Cache is cleaned first; failures are skipped and result can still be `ok`. Fallback factory is unbound. | Build a complete staged snapshot and swap only on success; one version; fail clearly on read errors. This is the legacy local provider, not the Fly SSH connection path. Its route is additionally gated by runtime-metering policy. |
| P2 / Synchronize target probe | `platform/synchronize/engine.py:_target_exists_as_file` | Builds another engine, reads the whole file for existence, catches all failures as `False`. | Inject resources; bounded stat in operation read; only actual absence means false. |
| P2 / Table reads | `content/table/dependencies.py:require_table_action` → `service.py:_get_ops` | Content/revision already share a reader, but dependency grant is discarded and `for_user` reauthorizes. | Inject the request grant while preserving named read/write action enforcement and CAS. |
| P2 / Scope suggestions | `platform/repository_target/scope_router.py:auto_suggest_scopes` | One bound one-shot read, so not an N+1 problem; broad exceptions become empty suggestions. | Preserve error semantics; a single read alone is not evidence of redundant snapshots. |
| Dormant helper | `platform/access/adapters/agent/sandbox_session.py:prepare_sandbox_data` | No caller found in runtime or tests. Contains stat/tree/per-file one-shot reads and skipped failures. | Remove after ownership review or migrate before reuse; do not report it as current Fly/Agent runtime traffic. |

These are performance, consistency, dependency wiring and error-semantics gaps.
An unbound adapter rejects access; this audit does not claim unauthorized data
was returned. Fixing performance must not replace that denial with a privileged
service identity.

## Already composed or deliberately separate

- Web file reads use `_product_read`; CLI and shared filesystem query helpers
  receive a `ProductReadSession` even where the variable is named `ops`.
- Download owns its pin through streaming and disconnect cleanup. Native
  history and derived projection read paths already use explicit snapshots.
- GitHub export/import helpers already use one explicit reader for related
  tree/content/revision reads. Their remaining issue is worker-engine resource
  construction and, for export, eager content materialization, not per-file pins.
- Tool path validation performs a single grant-bound stat. Individual read
  methods are not themselves evidence of N+1 work.
- Writes, worker admission/renewal and final publication need their own current
  policy/CAS checks. Do not remove these to force a global “auth once” metric.
- SSH/workspace native filesystem reads are a separate protocol boundary;
  `path.stat` in SSH key validation is local disk I/O, not a Version Engine read.

## Resource construction inventory

The recursive AST inventory records 17 factory references in 15 source files.
Process lifespan, Agent worker `serve`, bootstrap providers and composition
fallbacks are distinguished from operation-level construction. Remaining
operation-level sites include authentication/demo initialization, landing claim,
initialization reconciliation, leased worker commands, Synchronize probe and
GitHub import/export, local SyncWorker fallback and derived maintenance-job
fallbacks. Each needs caller-lifetime review and explicit injection before its
factory can be removed; simply deleting worker bootstrap is not safe.

The same inventory records read-surface references in 42 source files. It includes
legitimate native readers and local filesystem stat calls so new receivers or
directories cannot evade review through a narrow filename allowlist. The
`known_gaps` section names unresolved function-level debt explicitly.

## Executable checks and limits

1. `tests/security/entrypoint_audit.py` and
   `test_unified_authorization_architecture.py` walk the real FastAPI dependency
   graph and nested typed body models. Recognized Project/child-resource routes
   must appear in the authorization manifest; removed routes cannot leave stale
   contracts. Existing manifest coverage passes the expanded traversal.
2. `test_operation_entrypoint_audit.py` recursively scans all `src/**/*.py`,
   checking exact factory/read references and counts against the reviewed JSON
   inventory. Import aliases, module-qualified factories, new directories,
   offloaded method references and added reads have negative-control tests.
   Removed debt must be removed from the inventory, not hidden behind wildcards.
3. Public Synchronize forwarders are checked against operation dependency
   signatures. A new `Depends` parameter cannot silently remain unresolved in a
   direct Python call.
4. `test_synchronize_query_lifecycle.py` drives the real public HTTP route with
   the actual ProductOperationAdapter and native snapshot fixture, for SHA-1
   and SHA-256. It rejects one-shot adapter calls and repeated `for_user`, checks
   one authorization and one begin/release, changes HEAD during content read,
   and covers missing files, Viewer denial and foreign-project denial. Reads
   execute off the event-loop thread. Before the fix, four cases failed with
   `AttributeError: Depends ... for_user`; after the fix all eight pass.

Static reference counts are review triggers, not runtime request budgets. One
call site inside a loop can execute N times. Reflection, changed control flow
using identical receivers, opaque dict bodies, WebSocket/SSH and queue entry
semantics are not proved by this HTTP/AST guard. Those require protocol-specific
negative authorization, lifecycle and measured-budget tests. Existing Git/SSH/
worker tests remain in the full regression; this audit does not certify all
future protocols or all existing routes as one-authorization/one-snapshot.

No runtime bypass was added to make tests pass. No database schema, migration,
remote push, hosted deployment or service restart is part of this audit.
