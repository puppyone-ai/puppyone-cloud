# Workbench product-chain acceptance

This suite follows the accepted `refactor-cloud-workbench-ownership` change.
It does **not** change ISSUE-118 A1–A6 or turn component results into closure.

## Evidence levels

1. Existing runtime/SQL/Node/BuildKit suites: component regressions. Synthetic
   owners, direct SQL task setup and stock-Git CGI are useful here only.
2. The executable prerequisites below: production-entry and actual Pi/file
   behavior. They intentionally fail on the current implementation; neither is
   a complete product chain.
3. Product chain: one invocation and one private workspace created through the
   production creation entry, default service/worker composition, native Git
   authority and actual provider. **Not implemented/run yet.**
4. Provider-specific lifecycle/network/metering acceptance: separate Docker and
   E2B results. Local evidence never implies E2B acceptance. No paid execution is
   authorized by this test suite.

## Required continuous scenario (all still required)

| Phase | Required executable assertions / evidence |
| --- | --- |
| A1 creation and admission | Create via production workspace API; concurrent duplicate request and lost response resolve to one ID/resource/initial checkout. Persist allocation before side effects and confirm readiness by CAS. Before-ready and missing-selection Chats reject without creating/waking/queuing legacy work. Same-Project second user cannot attach. Start two independent Chats through the real API and **default** dispatcher. |
| A2 shared execution | Correlate owner/workspace/generation/resource and both task IDs. Actual Pi write/edit/Bash and ordinary-user terminal observe the same directory and branch. Private `0600` temp-file rename remains readable without chmod/copy. Stop/completion/failure/takeover and detached-child cleanup preserve the other Agent, terminal, preview and current unsaved edits. Old generation/controller cannot act after replacement. |
| A3 publication | The same initialized workspace sends actual Git bytes through the production private relay and Version Engine to owned PostgreSQL/object storage, for SHA-1 and SHA-256. Check original candidate/base/ref/operation receipts after ACK loss and later local/remote HEAD movement; competing ref CAS, explicit display following, hostile config and capability isolation. No stock CGI substitute here. |
| A4 live interaction | Use authenticated file/watch/PTY/preview **product endpoints**, not `docker exec` in their place. Verify writes, watch cursor/reconnect, PTY input and preview content in the same workspace. Revocation and generation changes invalidate all old capabilities. Published/history reads do not wake compute. |
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
admission/authorization and owned SQL/native object storage. Only the authenticated
subject is supplied by a fixture; the service, workbench owner, readiness and
publication are **not** replaced. It seeds identities and a published repository,
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
Current expected **failures**, not expected-pass assertions: missing selection
gets HTTP 202 and queues legacy work; explicit selection gets HTTP 503
`cloud_workbench_unavailable` instead of ownership admission.

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

Both cases currently fail with permission denied. Unsupported environments fail
rather than skip green. This does not allocate through the platform creation API,
exercise product terminal authorization, or establish provider acceptance.
Keep the existing six process-stop scenarios and twelve initialization/Git cases
as supplementary regressions; their passing cannot cancel these failures.

## Activation

Do not activate or close the feature from these prerequisites. Complete the
production creation/lifecycle/default composition and the continuous scenario
above before declaring product-chain success. The current prerequisites can be
fixed independently, but making them green alone cannot satisfy A1–A6.
