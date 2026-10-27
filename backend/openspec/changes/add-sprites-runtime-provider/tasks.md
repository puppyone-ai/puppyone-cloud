## Implementation
- [x] Implement the registered provider and bounded async exec transport.
- [x] Package and bootstrap the existing immutable worker on persistent storage.
- [x] Verify ownership, pause/resume, cleanup, transport and configuration contracts.
- [x] Document activation, preservation semantics and hosted acceptance limits.
- [x] Run applicable checks and record the results.

## Verification notes
- Final backend regression: **3578 passed, 956 skipped, 420 deselected**
  (`not e2e and not integration and not network`).
- 46 focused provider/archive/default/configuration tests and 44
  admission/readiness/security tests passed.
- 22 real PostgreSQL/PostgREST ownership/opening/dispatch integration tests and
  the 4 native Agent/editor file container scenarios passed.
- The actual linux/amd64 bundle passed isolated-container installation, retry,
  read-only management-socket isolation, firewall, native file save/read, holds,
  remount/restart preservation and artifact replacement rejection. See
  `sprites-local-receipt.json`. Final bundle SHA-256:
  `6035238f9bc6fcf69d3c5eb27f4e07becf3a78e585ade4f7b1e16417cbbc6a93`.
- **Both hosted product scenarios passed on clean source `3f1dd2b8`:**
  `sprites-hosted-warm-receipt.json` (110.90 seconds) and
  `sprites-hosted-restore-receipt.json` (180.15 seconds). Real JWT authorization,
  Agent execution, concurrent editor reads/writes, watch, task isolation, Git
  publication, consecutive-session resource reuse, paused reads returning 409,
  explicit reopen and saved-file preservation were exercised. The model alone
  was scripted; provider, worker, file APIs and local databases/storage were real.
- The restore scenario used the official checkpoint API on its exact paused
  fixture and verified that an unrelated process disappeared before product
  reopen. It proves recovery after runtime reconstruction, not a natural cold
  eviction or new kernel boot. Natural cold timing is provider-controlled and
  remains an optional soak check. Normal runtime pause/resume uses no checkpoints.
- Every owned test/probe Sprite was deleted and absence verified. The receipts
  contain no provider credential. Billing and large-scale concurrency were not
  tested.
- Hosted acceptance found and fixed three concrete issues: read-only platform
  sockets require a root-only bind alias and a masked public path; stale metadata
  needs bounded read retries and must not skip hold release; cached running
  admission after pause must return the existing 409 conflict instead of 500.
- Read-only preflight with Qubits' actual local configuration passed Sprites
  authentication/artifact and authorization/workspace SQL contract checks.
- Scoped Ruff/format, Node syntax, secret absence in changed tracked files,
  `git diff --check` and OpenSpec strict validation passed.

## Enablement gates
- [x] Build and export the actual worker bundle successfully.
- [x] Run hosted acceptance, including task management-socket isolation, native
  file edits and warm pause/reopen preservation; separately verify recovery after
  provider-level process reset from the exact saved filesystem.
- [x] Make Sprites the source/example default and implement retained-provider
  admission, Agent capability checks, open dispatch and maintenance for old Fly
  bindings. Focused cutover/provider regressions: 70 passed.
- [x] Run the completed integration checks and merge the verified commit into qubits.

## Local activation
- Verified code and hosted receipts were fast-forwarded into local `qubits`
  at `b69646c8`. The existing frontend edit was preserved.
- The ignored local backend environment now selects `sprites`, retains `fly`,
  and uses the verified bundle above. The full authenticated token is stored only
  in that local environment file with mode 0600; it is absent from tracked changes.
- The old local API and worker were stopped gracefully. The existing paired
  workspace launcher restarted both from the same configuration snapshot.
  Both preflights selected Sprites; the Agent/sandbox worker startup reported
  provider `sprites`, and the local API returned HTTP 200 with status `ready`.
- No remote branch push or hosted application deployment was performed.
