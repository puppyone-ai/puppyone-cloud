# Operation lifecycle verification

## Entry coverage audit follow-up — 2026-10-24

The recursive entry/read audit is recorded in [entrypoint-audit.md](entrypoint-audit.md).
It explicitly retains unresolved legacy paths; passing regression is not complete
adoption of the operation boundary.

- Expanded HTTP coverage walks nested dependencies and typed request models.
- Whole-source factory/read-reference inventory includes import aliases and new
  directories, exact reference counts, stale-entry checks and named migration debt.
- Public Synchronize dependency forwarding and one-authorized-snapshot behavior
  now have regression coverage. Four SHA-1/SHA-256 success/missing cases failed
  before the wiring fix; all eight success/missing/denied/foreign cases pass after.
- Focused audit, lifecycle and Synchronize checks: **94 passed**.
- Required local suite, retaining the baseline's existing 17 modified files:
  **3,689 passed, 956 skipped, 439 deselected, 0 failed/errors**, 154.26 seconds.
- New audit/test modules pass Ruff; `git diff --check` passes. No SQL/schema,
  migration, remote push, hosted deployment or service restart. The external
  database/runtime activation status below has not been reverified by this audit.

## Original lifecycle delivery

Status: implemented, locally regression-tested and integrated into qubits; runtime activation blocked by the test database HTTP dependency.

Baseline: `bc8714ec`, plus the canonical workstation's pre-existing uncommitted changes (preserved separately during development).

- Canonical ASCII architecture and ownership design updated first in `puppy-issues:document/puppyone/architecture/06-data-access-contracts.md`; documentation validator passed.
- Shared Search/Tool/Table and related route dependencies use the injected Version Engine. FastAPI dependency-graph tests prove shared clients and separate request authorization/Table state.
- CLI/shared filesystem reads own a complete read session; read-only session cannot publish or silently widen a Scope.
- Download validates signed target and current principal, and retains one pinned version through streaming/disconnect.
- Cooperative worker cancellation is shared in `infra/execution`; mandatory release is separate from cancelled business work. Git publication/durable tasks retain their owners.
- Isolated snapshot-port tests: stat/list/content/revision/timestamps use one begin/release; 35 repeated file reads use one begin/release and four distinct object reads. These are port counts, not network measurements or latency claims.
- Initial sandbox run could not create local test sockets or inspect processes. Re-run with permission for local fixtures completed 3,644 tests, with only the pre-existing OpenAPI History contract fixture failing. The original canonical branch independently reproduced that failure. An exact four-entry History delta preserves historical fixtures and all unrelated route/schema checks.
- Focused cancellation, lifecycle and history cache run: 37 passed, 1 existing skip.
- No SQL/schema migration, remote push or hosted deployment in this change.

OpenSpec CLI is not installed in this environment. Proposal/design/tasks and requirement scenarios were checked structurally; do not report a CLI validation receipt.

## Final local regression

- Candidate implementation `e94bd80a` plus the canonical branch's existing 17 modified files, applied with a clean three-way merge.
- Repository-required command: `python -m pytest -q -m 'not e2e and not integration and not network' --tb=short --disable-warnings`.
- Result: **3,672 passed, 956 skipped, 439 deselected, 0 failed, 0 errors**, 149.59 seconds. Skips/exclusions are not claimed as end-to-end acceptance.
- Additional explicit in-flight ZIP cancellation test (SHA-1 and SHA-256), added after collection: lifecycle file **18 passed**, 0.71 seconds. It waits for the running blob read before closing the pin.
- New execution/query/injection modules and tests: Ruff passed. `git diff --check` passed. Existing unrelated style warnings were not used to rewrite other domains.
- Structural OpenSpec checks passed; CLI unavailable, so no `openspec validate` success is claimed.

The implementation does not certify every pre-existing synchronous call in every domain as optimized. This change closes the repeated process-engine construction in the listed request dependencies, composes CLI/shared-filesystem reads into one lifetime, and fixes download ownership. Long indexing pipelines, whole-history diff algorithms and other independent business operations retain their separate budgets and acceptance work. Web synchronous reads retain FastAPI threadpool execution; new queue settings apply to `run_read` consumers, not all backend traffic.

## Local integration and runtime activation

- Local `qubits` fast-forwarded to `5f2d6b17`. Autostash restored all 17 pre-existing modified files; their final bytes and complete diff match the tested candidate exactly. Only this task's isolated worktree/branch was removed after that check.
- Supervisor detected the source change and restarted its API/worker group. Its existing workspace preflight exceeded 40 seconds and exited. A restart using the same command and unchanged `.env` reproduced the timeout; the local API is therefore not currently listening on port 9090.
- Read-only diagnostics: Fly provider preflight passed in 1.033 seconds. The authorization, workspace-pending and workspace-contract database checks each exceeded the diagnostic 15-second bound.
- Without credentials, the same Supabase HTTP endpoint returned 401 in 0.808 seconds direct / 1.575 seconds through the existing proxy. Raw authenticated HTTP requests bypassing the application and Supabase SDK timed out: empty `projects` select 8.672 seconds; absent-identity authorization RPC 8.671 seconds; proxy empty select 8.618 seconds.
- This isolates the current activation blocker to authenticated test-environment PostgREST requests. It does not establish whether the remote cause is a database lock, connection pool or platform failure. No database mutation, migration, remote restart or authorization bypass was performed.
- Local functional regression success and source integration are complete. Live service/desktop acceptance remains blocked until that external dependency responds; do not claim the backend is running or a hosted deployment succeeded.
