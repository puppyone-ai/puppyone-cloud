# Operation lifecycle verification

Status: local candidate; integration receipt is completed after the final regression run.

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
