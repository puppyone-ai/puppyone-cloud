## 1. Behavior-restoring fixes (approval exception)

- [x] 1.1 Add real React tests for disabled/loading/error/empty structured queries.
- [x] 1.2 Remove the effect feedback loop and stabilize workspace actions.
- [x] 1.3 Add ASGI/event-loop concurrency regressions for activity/history reads.
- [x] 1.4 Offload synchronous I/O at audited async read boundaries, preserve auth.

## 2. Architecture implementation (after design approval)

- [x] 2.1 Document frontend directory/dependency/state ownership with boundary tests.
- [x] 2.2 Extract workspace navigation/session ownership and project isolation.
- [x] 2.3 Consolidate shared query keys, pending-conflict reads and invalidation.
- [x] 2.4 Bound diff work/rendering; extract tested feature logic.
- [x] 2.5 Standardize read cancellation/deadlines and request correlation.
- [x] 2.6 Add lightweight head synchronization with reconnect lifecycle/page coverage.
  Existing empty/pruned-anchor and long invisible-run limitations are recorded in
  `read-model-contracts.md`; they require the separate pagination contract.
- [x] 2.7 Define/review history pagination and authorized snapshot DB contracts.

## 3. Verification and handoff

- [x] 3.1 Run targeted frontend tests/typecheck and backend regression suites.
- [x] 3.2a Run production build (isolated `.next-check` directory).
- [ ] 3.2b Run authenticated production-mode browser route/draft/Chat measurements.
  Browser-control bridge reports no browsers/native pipe startup failure; do not
  substitute component tests or unauthenticated HTTP smoke checks for this gate.
- [x] 3.3 Update architecture documentation, record remaining risks and actual results.
- [x] 3.4 Validate proposal using OpenSpec when tooling is available.

## Verification receipt (2026-09-19)

- Frontend: 54 runtime/unit/boundary assertions across 12 test files; TypeScript;
  focused ESLint; BFF/hydration/deployment contract scripts passed.
- Clean `npm ci` passed in a fresh temporary frontend copy with no env files or
  node_modules. It exposed a missing nested SWC helper peer in the npm 11 lock;
  regenerated the lock with the repository's npm 10 before rechecking.
- Git history selection reconciliation waits for a real snapshot; loading gaps
  no longer clear stored filters/selection. Expansion resets only on changed
  filters, not view remount. Runtime regressions cover these state transitions.
- Access and Chat now occupy one project-layout-owned, resizable right sidebar.
  Header actions switch the mutually exclusive panel immediately; Access is no
  longer a primary view, while its old URL remains a compatibility adapter.
- Backend: 1,178 tests passed (Version Engine, read concurrency, authorization,
  Office); 26 existing warnings. Local protocol tests require loopback sockets.
- Next production build passed; Files first-load JS 431 kB and Changes 212 kB.
  The `/access` compatibility adapter is 107 kB; its actual sidebar manager is
  loaded on first open. These are build snapshots, not measured navigation gains.
- Canonical documentation validator passed: 318 Markdown / 201 governed docs;
  25 existing cross-repository link warnings remain. OpenSpec strict validation
  passed using the ephemeral CLI, without adding backend Node dependencies.
- HTTP smoke: local Web login 200, protected backend head 401 without auth.
- No authenticated browser performance result: no available browser surface.
- npm audit: 25 pre-existing affected dependencies including critical Next;
  affected paths/versions matched the baseline lock. Security upgrade is not
  bundled into this architectural change; no `audit fix --force` was run.
- No commit, push, deployment, production migration or production data write.

## 4. Staged loading follow-up (user approved 2026-09-19)

- [x] 4.1 Explicit loading/ready/error states; remove synthetic partial rail list.
- [x] 4.2 Keep same-key snapshots on refresh, isolate org/project/path transitions.
- [x] 4.3 Prioritize active-root listing; defer seven file decoration reads.
- [x] 4.4 Lazy member directory, user-scoped org discovery/preferences and selection.
- [x] 4.5 Lightweight project list skips access counts without changing grants.
- [x] 4.6 Project-scoped expansion, file error/retry and pending/failed-save guard.
- [x] 4.7 Add delayed/reordered/failing query and authorization regressions.
- [x] 4.8 Verify clean build and update canonical architecture/evidence receipt.

### Staged loading verification receipt

- Frontend: 81 tests across 17 files; TypeScript and targeted ESLint passed.
- Backend: 35 targeted navigation/authorization/manifest regressions passed
  (24 existing dependency warnings). This slice did not repeat all backend tests.
- Clean isolated `.next-check` production build passed. An initial reused build
  cache referenced a removed chat-parity fixture; moving that generated cache
  to a temporary backup and rebuilding fixed the check without restoring it.
- OpenSpec strict validation passed. Canonical docs describe the staged reads,
  tests and deferred limits; no measured end-to-end percentage is claimed.
- Native browser inspection reached a separate chat debug page, not a verified
  workspace-loading flow. Delayed/error states are covered by React runtime
  tests, not claimed as an authenticated browser performance measurement.
- Local Next dev and backend uvicorn --reload remain running; no restart,
  production migration, commit, push or deployment was performed.
