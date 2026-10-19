# Workbench product-chain acceptance

This suite covers `refactor-cloud-workbench-ownership` and its follow-up
`complete-agent-workspace-runtime`. The latter supplies default user/Project
admission, provider ownership and native file APIs.
It does **not** change ISSUE-118 A1–A6 or turn component results into closure.

## Evidence levels

1. Existing runtime/SQL/Node/BuildKit suites: component regressions. Synthetic
   owners, direct SQL task setup and stock-Git CGI are useful here only.
2. The executable prerequisites below: production-entry and actual Pi/file
   behavior. Native-file and authenticated production-entry prerequisites pass
   locally. Neither is a complete product chain.
3. Product chain: one invocation and one private workspace created through the
   production creation entry, default service/worker composition, native Git
   authority and actual provider. `accept_fly.py` implements the scoped Fly
   runtime scenario below; it passed on real Fly resources on 2026-10-19.
4. Provider-specific lifecycle/network/metering acceptance: separate Docker,
   E2B and Fly results. Local evidence never implies hosted-provider acceptance.
   The user authorized disposable Fly resources for this test; opt-in remains
   mandatory so routine regression jobs cannot incur provider charges.

## Required continuous scenario (all still required)

| Phase | Required executable assertions / evidence |
| --- | --- |
| A1 creation and admission | Create via production workspace API; concurrent duplicate request and lost response resolve to one ID/resource/initial checkout. Persist allocation before side effects and confirm readiness by CAS. Chats without an explicit selection resolve the same private default workspace; the lifecycle owner ensures readiness before task attachment. Invalid ownership rejects without queuing legacy work. Same-Project second user cannot attach. Start two independent Chats through the real API and **default** dispatcher. |
| A2 shared execution | Correlate owner/workspace/generation/resource and both task IDs. Actual Pi write/edit/Bash and ordinary-user terminal observe the same directory and branch. Private `0600` temp-file rename remains readable without chmod/copy. Stop/completion/failure/takeover and detached-child cleanup preserve the other Agent, terminal, preview and current unsaved edits. Old generation/controller cannot act after replacement. |
| A3 publication | The same initialized workspace sends actual Git bytes through the production private relay and Version Engine to owned PostgreSQL/object storage, for SHA-1 and SHA-256. Check original candidate/base/ref/operation receipts after ACK loss and later local/remote HEAD movement; competing ref CAS, explicit display following, hostile config and capability isolation. No stock CGI substitute here. |
| A4 live interaction | Use authenticated file/watch/PTY/preview **product endpoints** (public PTY and running-app preview endpoints remain a separate incomplete surface), not `docker exec` in their place. Verify writes, watch cursor/reconnect, PTY input and preview content in the same workspace. Revocation and generation changes invalidate all old capabilities. Published/history reads do not wake compute. |
| A5 activity and accounting | Exercise active Agent, interactive human, background lease, idle and no-Run cases. Prove one workspace compute charge, separate Run model usage, pause/wake intervals and any holding charges. Disabled metering cannot pass this phase. Polling alone cannot renew activity. |
| A6 retention and reconstruction | Clean only registered scratch. Preserve ambiguous receipts, unpublished/ignored files and required memory. Reconstruct only after explicit recoverability proof. Measure actual connection/readiness/interaction/recovery durations; unavailable snapshot bytes remain unknown, not zero. Verify provider/resource cleanup and no lost responsibility after failure or restart. |

For closure, record source commit + dirty/tree identity, image digest/template +
SDK version, request/workspace/generation/task/operation IDs, native receipt/ref
results, stage outcomes and cleanup results in the same reproducible run. Never
record bearer/provider secrets. A failure blocks dependent stages; unexecuted
stages stay unverified. Zero collection, skipped/xfail cases, helper success,
configured CI or six independently fabricated ready states are not a PASS.

Only external model responses may be deterministic in the local composed chain;
Pi, tools, authorization, lifecycle/readiness, dispatcher, publication, interaction
and metering behavior must remain production implementations. Infrastructure
may be owned PostgreSQL/PostgREST/MinIO. An identity-decoding fixture does not
prove real JWT authentication and must be declared separately.

## Executable production-entry prerequisites (A1)

`accept_production_entry.py` is explicitly invoked, not hidden behind xfail or
converted into a test that expects the bug. Its non-default filename separates
an unfinished product acceptance gate from existing component regression jobs.

It constructs the actual FastAPI app and default `get_agent_service`, with real
admission/authorization and owned SQL/native object storage. Test identities use
locally signed JWTs and production token verification; no authentication or Agent
service dependency is replaced. It seeds identities and a published repository,
never a ready workbench, Run/task or resource receipt. It does not start app
lifespan/dispatcher, provision compute or run inference. Metering is disabled:
this is an entry check, **not** A5 acceptance.

From `backend/`, use already-installed local tools (do not download or use hosted
services). Supply `PUPPYONE_TEST_POSTGREST_BIN` and `PUPPYONE_TEST_MINIO_BIN` as
absolute paths to owned test binaries, and PostgreSQL 17 on PATH:

```sh
export SUPABASE_URL=http://127.0.0.1:9 SUPABASE_KEY=workbench-local
export INTERNAL_API_SECRET=workbench-local AWS_EC2_METADATA_DISABLED=true
export SANDBOX_TYPE=docker MANAGED_AI_ENABLED=false RUNTIME_METERING_MODE=disabled
export E2B_API_KEY='' CLOUD_AGENT_TEST_E2B=0 OPENAI_API_KEY='' ANTHROPIC_API_KEY=''
python -m pytest -q tests/agent/workbench_acceptance/accept_production_entry.py \
  --junitxml=/tmp/workbench-entry.xml
```

The fixture enables admission locally without starting an inference consumer.
The updated assertions require two Chats with no explicit workspace selection to
share one private default binding, with different users isolated. This entry
prerequisite passed against owned PostgreSQL/PostgREST/MinIO on 2026-10-19.
It does not exercise Supabase login or token issuance.

## Real Fly runtime scenario

`accept_fly.py` uses authenticated HTTP, the default dispatcher/lifecycle owner,
real Fly Machines/Volumes/SSH, Pi/Bash, live file read/write/watch, and native Git
publication into owned PostgreSQL/MinIO. It asserts saved files are visible
while Bash is still running and before publication, editor conflict handling,
two independent Chats sharing one Machine, and stop/reopen preserving files.
Task cleanup must be acknowledged before either Run becomes terminal. The test
also checks rejected IPv4/IPv6 task egress against the live firewall counter.
The teardown deletes only the test Project's own Machines and Volumes and emits
a receipt with source/image/resource identities and completed stages.

Model responses are deterministic; metering is disabled. This is a runtime
provider test, not A5 billing, browser rendering, PTY, or complete A1–A6 acceptance.

Provision a disposable Fly app and publish `agent-worker/Dockerfile` for
`linux/amd64` to its registry. Set `CLOUD_AGENT_FLY_APP`, `CLOUD_AGENT_FLY_TOKEN`
and `CLOUD_AGENT_FLY_IMAGE=registry.fly.io/<app>@sha256:<digest>` locally; never
commit credentials. API and worker deployments install pinned flyctl 0.4.118.
Other hosts can run `python scripts/install_flyctl.py /absolute/path/to/fly` and
set `CLOUD_AGENT_FLY_CLI` to that path. Use the owned service binaries above.

```sh
CLOUD_AGENT_TEST_FLY=1 CLOUD_AGENT_FLY_TEST_RECEIPT=/tmp/fly-runtime.json \
  python -m pytest -q -s tests/agent/workbench_acceptance/accept_fly.py \
  --junitxml=/tmp/fly-runtime.xml
```

Result: **1 passed** on real Fly Machines/Volumes/SSH in `lax` (200.15 seconds,
including fixture setup), on clean source `77f94f29`;
[the receipt](fly-runtime-receipt.json) records its hash, image digest, both Run
IDs, Machine/Volume identities, native publication and cleanup. Model replies
were deterministic; no paid inference was called. Full required backend
regression on the same implementation: 3,252 passed, 885 skipped, 399 deselected.

The live run exposed and fixed enriched mount-response validation, unsupported
iptables owner matching, boot-readiness admission, and heartbeat/cleanup races.
Fly now uses native nftables; an unconfigured firewall prevents boot. Only GET
transport failures receive bounded retries; mutations retain their existing
reconciliation contract. A fresh cleanup controller starts a fresh frame
sequence, and an acknowledged park cannot race a later task heartbeat.

Cold readiness in this Chat chain measured 64.66 seconds. The previous `520b5670`
run in `sin` took 16.17–25.90 seconds per file request (median 19.36). Bound
connection handles and bounded channel/helper reuse now remove repeated setup
from warm requests. The separate I/O measurement below also changes region and
uses optional read compression; it is not a controlled, single-variable speedup
comparison. First connection and cold startup remain slow.

Machine absence and Volume deletion are asserted. Fly exposes soft-deleted
Volumes as `pending_destroy`; these are no longer billed, per
[Fly's billing contract](https://docs.fly.io/about/billing#volume-billing).
This receipt is scoped runtime evidence, not complete A1–A6 or hosted activation.

## Real provider file I/O

`accept_io.py` exercises the production authenticated HTTP routes against owned
PostgreSQL/PostgREST/MinIO and real provider resources. It measures allocation,
first file-channel connection, then 50 write/read/list iterations on a 4,096-byte
compressible ASCII text file (counter plus dot padding). It verifies live content,
one reused channel, stale-save rejection, unauthenticated rejection, paused reads
without wake, and file retention plus a new channel after explicit resume.
The next run also records reopening an already-running workspace and the first
file read after resume, checking that both retain the same resource identity.
The existing Fly receipt predates these additional daily-use timing fields.
The workload uses no model and disables metering. Resource cleanup is asserted.

Use the owned service binaries and credentials described above. For E2B, provide
`E2B_API_KEY` and `CLOUD_AGENT_E2B_TEMPLATE` built from the candidate worker. For
Fly, provide the app, token, pinned image, CLI and region settings above. Create
the receipt directory first. Opt in to one provider or both (`fly,e2b`):

```sh
CLOUD_AGENT_TEST_IO=fly CLOUD_AGENT_IO_RECEIPT_DIR=/tmp/provider-io \
  python -m pytest -q -s tests/agent/workbench_acceptance/accept_io.py
```

The [Fly I/O receipt](fly-io-receipt.json) records clean source `77f94f29`, pinned
image, all 150 samples and cleanup. The API ran on the local test host, with a
real `lax` Machine and an app-token tunnel isolated from cached user CLI state.
After both runs, the disposable Fly app was deleted, its temporary deploy token
revoked, and local copies of the temporary credentials removed.
Each operation passed the p50 ≤300 ms / p95 ≤1,000 ms goal:

| Warm operation | Samples | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| Write | 50 | 265.88 ms | 285.12 ms | 324.69 ms |
| Read | 50 | 241.06 ms | 254.28 ms | 273.98 ms |
| List | 50 | 242.82 ms | 277.09 ms | 485.00 ms |

Allocation/open took **46.81 s**, the first read/channel connection **18.92 s**,
and explicit resume **21.17 s**. Control-only round trips measured p50 239.89 ms.
These warm results do not establish cold-start, large-file, incompressible-data,
watch-delivery or other-region latency. The native Linux regression separately
checks compressed/plain byte and revision equality, persistent helper reuse and
helper failure without replay. No file content is cached.

E2B uses the same executable workload, but **has no current same-workload receipt**.
Building its candidate template requires uploading worker source to E2B;
automatic approval review rejected that upload pending explicit user approval.
Earlier E2B evidence does not establish these new I/O targets or a speed ranking.

## Executable private-file prerequisites (A2)

This invokes the real worker image, lifecycle initializer, task controllers,
Pi SDK and Bash tools. The second Agent reads the actual first Agent's saved
file, not a root/test-created copy. An ordinary terminal uses the workspace's
file-owner UID. No model API, external network, provider VM or host mount is used.

```sh
docker build --pull=false --network=none --no-cache --progress=plain \
  --build-arg WORKBENCH_AGENT_IMAGE=YOUR_LOCALLY_BUILT_CANDIDATE_IMAGE \
  -f agent-worker/WorkbenchFileAcceptance.Dockerfile agent-worker
```

Three native-file cases pass in Linux BuildKit: private atomic saves shared by
actual Pi tasks, ordinary-user access, and file/watch channel visibility before
Git publication. Unsupported environments fail rather than skip green. This does not allocate through the platform creation API,
exercise product terminal authorization, or establish provider acceptance.
The six process-stop scenarios and twelve initialization/Git cases also pass
as supplementary regressions. They do not establish full product-chain acceptance.

## Activation

Do not activate or close the feature from these prerequisites. Complete the
production creation/lifecycle/default composition and the continuous scenario
above before declaring product-chain success. The current prerequisites can be
fixed independently, but making them green alone cannot satisfy A1–A6.

## Local candidate evidence (2026-10-19)

Candidate: `codex/agent-workspace-runtime`, based on local `qubits` `f4eccb9058f7e8ab859b4f88b2b0abf79905dc2c`,
with the publication/lifecycle work from `codex/issue-118-completion` `a61435c5`.
These results describe the source candidate at this evidence checkpoint;
subsequent source integration does not establish a deployed/accepted release.

- Required backend regression on `77f94f29`: 3,252 passed,
  885 skipped, 399 deselected. Provider-contract unit cases use mocks; the
  separate real Fly receipt above establishes the scoped hosted runtime chain.
- Final owned PostgreSQL/PostgREST/MinIO authorization, lifecycle and admission
  suite: 34 passed. Final native Linux file suite: 3 passed. The separate Fly
  runtime chain and I/O workload each passed on the same clean implementation.
- Provider-only extension, resource-bound handles, channel lifecycle and bounded
  file-transfer contracts are covered by the backend regression. Final Python
  lint/format, Node syntax and strict OpenSpec validation passed.
- Earlier owned PostgreSQL/PostgREST combined lifecycle, authorization fencing, legacy
  preservation, populated migration rollback/reapply: 88 passed. Auth stubs and
  omitted Supabase-only extensions are declared by `native_postgres.py`.
- Earlier local ARM worker artifact: `sha256:499fdac5291718db41d9cb025fa0f9649cd4fee47ae53d3696ca2e243748e8b6`.
  Offline BuildKit: 6 process-stop, 12 Git/initialization and 3 native-file
  scenarios passed. Local Node protocol/Git regressions: 23 passed.
- Earlier frontend workspace: 351 passed; full frontend: 459 passed and one existing
  `login-page.test.tsx` email-sign-in initialization failure, reproduced on
  original `qubits`. TypeScript passed. Repository ESLint: no errors, 91 warnings.
  Dependency analysis adds no errors; the existing `apiClient.ts` to
  `features/auth/login-intent.ts` violation remains on both branches.
- Both OpenSpec changes validate strictly. Canonical documentation validation
  still reports two unrelated broken ISSUE-115/117 links in Desktop's
  `experimental-path-forks.md` and 25 existing cross-repository warnings.

Local target migration diff adds only `20261014040000` (publication target
binding from the predecessor branch) and `20261019010000` (workspace runtime).
No historical migration or data artifact is rewritten. The actual last accepted
hosted release/applied-version set has not been inspected.

The required full populated Docker/Supabase upgrade rehearsal is blocked:
new regular Docker containers remain Created while six existing user containers
are running. BuildKit works, but is not a substitute for the release rehearsal.
No existing user container was stopped. These historical results do not claim
a target-branch merge, push, production deployment, complete A1–A6 acceptance or hosted activation. Native
folder archive download, public PTY and running-app preview
endpoints remain outside this file API; regular file previews are connected.

The subsequent six-batch structure refactor and its actual-environment
acceptance are recorded in
[`refactor-git-mcp-scope-boundaries`](../../../openspec/changes/refactor-git-mcp-scope-boundaries/tasks.md).
The user requested local source integration before the remaining acceptance
gates were complete. The CLI admission failure and missing fresh provider
results remain explicit; this request does not close A1–A6 or activate hosting.
