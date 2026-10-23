# Read entry closure verification

Status: implemented and locally regression-verified; no hosted deployment or explicit service restart.
Baseline: local `qubits@51d5b53a`, retaining all 17 pre-existing modified files.

## Scope and evidence

- Eight `/internal/nodes/*` paths are unmounted, absent from OpenAPI and return
  404 regardless of supplied internal credentials. Their route dependencies do
  not execute. Source and obsolete tests are archived with non-importable,
  non-collectable extensions outside `src`; runtime import checks cover archive.
- The OpenAPI delta removes exactly those eight paths. No unrelated URL,
  schema or authentication-scheme fingerprint changes. Historical fixtures remain
  immutable; the retirement has a separate exact delta.
- A real native-reader fixture containing 35 distinct Markdown files and one
  JSON file indexes with one initial human authorization and one begin/release,
  for both SHA-1 and SHA-256. HEAD changes during embedding do not change the
  input version. Read/embedding events alternate, proving sequential consumption
  rather than eager whole-repository content loading. These are boundary counts,
  not real-service latency or aggregate network-request measurements.
- Invalid JSON, source failure, embedding failure, vector failure after a
  previously completed file, and cancellation all fail the task and release its
  pin. Cancellation during pin acquisition, native reads or chunk persistence
  joins the blocking worker before release. Existing per-file vector upserts are
  not a whole-task atomic index publication; already written entries may remain
  after a failure, and retry/rebuild is still needed.
- Operation authorization reuses facts for at most 30 seconds between protected
  steps, then revalidates against fresh canonical facts. Tests revoke access while
  an HTTP permission cache still contains the old grant, and verify denial.
  Concurrent checks share one refresh; different users retain distinct grants.
  Native pin renewal is not treated as a user-permission check.
- Agent metadata HTTP tests use the real ProductOperationAdapter and canonical
  policy: 35 visible Agents sharing three paths use one authorization, one pin
  and three metadata lookups. Missing paths return absent metadata; storage
  failures propagate. Denied requests perform no Agent/file read, and empty
  Agent lists open no pin. Visibility filtering still receives the current user.
- Tests exposed an existing `VersionEntry.id` access in indexing logs; the DTO
  has `path`, so this was corrected instead of swallowing the exception.

## Validation and delivery boundaries

- Focused entry, security, indexing and file-operation regression: 290 passed
  before the final cancellation/invalid-input cases were added.
- Initial full local suite: 3,711 passed, 956 skipped, 412 deselected, 0 failures,
  168.47 seconds.
- Final source, including owned chunk persistence and all additional failure cases:
  **3,719 passed, 956 skipped, 412 deselected, 0 failed/errors**, 162.89 seconds.
  Command: `python -m pytest -q -m 'not e2e and not integration and not network' --tb=short --disable-warnings`.
- Retirement archives ten obsolete POSIX unit cases and 27 legacy MCP E2E cases;
  those cases are not counted as successful acceptance of the replacement.
  Current MCP runtime/transport, native file and authorization tests remain active.
- New/modified lifecycle and indexing modules/tests pass Ruff. Documentation
  validation passes; the pre-existing 25 cross-repository link warnings remain.
- OpenSpec CLI is unavailable. Proposal/design/tasks and requirement scenarios
  are checked structurally; no CLI validation success is claimed.
- No SQL/schema changes, migration, remote push or production deployment. The
  prior external database/runtime activation blocker has not been reverified.
