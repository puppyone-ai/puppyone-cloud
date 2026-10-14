## Implementation (A1–A6 remain the closure scope)

- [x] Record the approved shared-computer model and tests before implementation.
- [x] Record the 2026-10-14 approval to remove pre-launch Chat compatibility.
- [ ] A1: Make selected-workbench admission the only production execution path.
  - [x] Independent durable identity, actor/project ownership and immutable attachments.
  - [x] Clarify private-user ownership; tasks borrow a running host without create/wake capability.
  - [x] Move initial Git/directory preparation from Chat control into a root-only lifecycle artifact; task protocol v2 attaches only.
  - [ ] Platform workspace creation API with idempotent provisioning and readiness CAS; explicit workspace wake.
  - [ ] Concrete admission and removal of the Session-owned default composition.
- [ ] A2: Complete production lifecycle and shared-file/process semantics.
  - [x] Durable task ledger, allocation intent, generation/version/operation fences.
  - [x] Integrate tasks and conversation-only recovery with the existing supervisor/dispatcher.
  - [x] Task revocation and confirmed cleanup fence late starts and pending Git.
  - [ ] Use one filesystem identity for the owner's Agents/human interactions; replace UID-based stop isolation with a verified task process domain.
  - [ ] Concrete provider owner for acquire/resume/pause/retire and production composition.
  - [ ] Real Docker/E2B lifecycle and shared-execution acceptance.
- [ ] A3: Complete native publication and document the production contract.
  - [x] Capture target ref from the locked workbench during Run submission; forbid retargeting.
  - [x] Explicit native ref CAS independent of display HEAD, original operation receipts and explicit native HEAD following.
  - [x] Publish the captured OID after another local commit, without re-finalizing or rewinding files.
  - [x] Preserve user Git configuration using a read-only control view over native shared metadata.
  - [x] Real Linux controller/terminal/config-adversary and stock Git HTTP tests, both object formats.
  - [ ] Integrate creation/current-branch lifecycle with publication; complete canonical documentation and full provider acceptance.
- [ ] A4: Authorized generation-bound file/watch, PTY, preview and revocation/reconnect.
- [ ] A5: Effective activity/background leases, no-Run ownership and one shared compute meter.
- [ ] A6: Managed scratch cleanup, measured recovery, unavailable snapshot-byte reporting and receipt/unique-copy-checked reconstruction.
- [ ] Complete canonical documentation, current ISSUE-118 audit and all relevant regressions.

## Current implementation boundary

The shared path is NOT activated. Production still supplies neither an admission
adapter nor a concrete WorkbenchOwner; the default worker still claims legacy
Runs. That is a temporary implementation gap, not the desired compatibility
contract. No new work should expand legacy Chat compatibility. Do not activate a
partial shared path or change an A1–A6 acceptance item to PASS from helper tests.

The ownership/task Expand migrations preserve durable cleanup responsibility;
installing them does not adopt or delete old resources. The new target-binding
migration affects new shared Runs only and does not rewrite old histories,
resource identities or repository HEAD. Populated upgrade and rollback tests
run through the full ordered local migration chain. Hosted applied versions and
release readiness have not been inspected or claimed.

Task isolation still uses distinct UIDs and is not sufficient for arbitrary
shared file permissions between the SAME user's Agents. Different users never
share a live workspace. The task controller no longer initializes directories or
Git: `workbench-lifecycle.mjs` now owns that artifact operation. It records the exact
initialization input, reconciles a completed checkout without resetting files, and
refuses readiness for uncertain/dirty partial initialization. Trusted state moved
out of user-owned `/tmp` into root-protected `/run/puppyone-workbench`. Task protocol
v2 rejects old artifacts which could clone on Chat start. None of this implements
the missing creation API, provider allocation owner or database readiness ACK.
The task host interface exposes check/connection only, and acquisition rejects
non-running resources before provider access. This does not complete provisioning
or remove the still-existing legacy production composition. The full-controller Git tests corrected an earlier
helper-only audit: the old wrapper did restore config mode after completion,
but a real concurrent terminal still failed during the 0600 window. Both that
window and config destruction are fixed by the shared control view. The separate
0600 temporary-file rename case remains open.

The tool-recovery line is already merged in the current 7e38c974 base. Its two
provider snapshot fixture failures lacked the required SDK disconnect method;
the fixture now supplies and asserts it, without weakening production behavior.

## Product-chain acceptance (not a sum of component results)

The continuous A1–A6 scenario and reproducible prerequisites are defined in
`tests/agent/workbench_acceptance/README.md`. It starts at platform creation and
uses the default API/worker composition, not a supplied test owner or SQL-written
`running` state. The same workspace then exercises two Chats, live interactions,
stop isolation, native publication/receipts, activity/metering and safe recovery.
All phase identities/results and cleanup must belong to that same run. Missing
phases remain unverified; component passes, skipped cases and CI configuration
cannot close the issue.

Two executable prerequisite suites intentionally expose current product defects:

- `accept_production_entry.py` uses actual FastAPI routing/default AgentService,
  authorization and owned native SQL/object storage, without a replacement
  service/admission/readiness/owner. Identity decoding is a declared fixture and
  app lifespan/worker/metering are not exercised. Missing workspace currently
  queues a legacy Run (202); explicit workspace gets an unconfigured-port 503.
- `workbench-files.acceptance.mjs` uses actual Pi/Bash tasks, not root-created
  file copies. Private atomic saves currently fail for both another Agent and
  the ordinary file user. The separate `WorkbenchFileAcceptance.Dockerfile`
  fails on these assertions; no xfail/skip turns them into passing evidence.

These are **red prerequisites, not implemented full-chain acceptance**. The
regular six stop/process scenarios still require unprivileged execution,
no-new-privileges and isolated detached-child cleanup; they no longer require
that task slot numbers themselves be filesystem UIDs.

## Evidence and reproduction

- `tests/agent/runtime/test_workbench_target_binding.py`: actual PostgreSQL
  admission capture, caller mismatch, immutable binding and later workspace ref changes.
- `tests/repository_hosting/unit/test_workbench_ref_publication.py` and
  `tests/agent/runtime/test_workbench_publication.py`: explicit ref selection,
  metadata budget, strict candidate/target, receipt-first retry and no HEAD fallback.
- `tests/agent/runtime/test_workbench_native_publication.py`: actual owned
  PostgreSQL/PostgREST/MinIO, native CAS/receipt and display following for SHA-1
  and SHA-256. No provider is created.
- `agent-worker/workbench-publication.test.mjs`: owned loopback stock-Git daemon;
  captured candidate survives newer commits and unsaved edits.
- `agent-worker/workbench-git.docker.test.mjs`: real shared controller, task UIDs,
  ordinary terminal Git, concurrent hostile config and stock-Git CGI transport.
  Creation now runs through the separate lifecycle channel before task attachment;
  regressions cover missing readiness, duplicate initialization, ACK loss, dirty
  partial-copy preservation and rejecting a user-planted root symlink.
  The CGI fixture is not the production Version Engine adapter; the separate
  native database/S3 tests validate that authority.
- `agent-worker/workbench-process.docker.test.mjs`: six real concurrent Pi/bash,
  detached-child, PTY, preview and revocation scenarios with deterministic model frames.
- `test_shared_supervisor.py` / `test_shared_dispatcher.py`: real database/control
  chain, with synthetic provider and publication ports. Not provider acceptance.

Build the pinned worker or use the owned cached image, then from `backend/`:

```sh
docker build --network=none --no-cache --progress=plain \
  -f agent-worker/WorkbenchAcceptance.Dockerfile agent-worker
node --test agent-worker/git.test.mjs agent-worker/protocol.test.mjs \
  agent-worker/workbench-relay.test.mjs agent-worker/workbench-publication.test.mjs
```

The acceptance Dockerfile copies candidate modules rather than trusting stale
source in a base tag. It executes the process and Git suites separately because
they own the same disposable image paths. CI supplies its freshly built worker
through WORKBENCH_AGENT_IMAGE. No host mount, paid model or external network is
used by these Linux scenarios.

Set `CLOUD_AGENT_TEST_E2B=0`, `E2B_API_KEY=''`, `MANAGED_AI_ENABLED=false`,
`RUNTIME_METERING_MODE=disabled` and loopback-only Supabase placeholders for local
Python runs. Owned PostgreSQL/PostgREST/MinIO fixtures do not use hosted services.
The latest owned `/bin/true` Docker start probe still timed out after 20 seconds;
its container was removed. BuildKit execution works, but cannot substitute for
Docker adapter or E2B lifecycle acceptance. No Docker restart or paid creation
is authorized. A separate owned BuildKit capability probe also found PID namespace
creation denied (`Operation not permitted`) and cgroup mounted read-only. This is
an environment-specific result, not E2B evidence or permission to broaden host
privileges. It does not block further local implementation.
