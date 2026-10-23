# Workspace opening pipeline verification

Status: local implementation verified; protected Qubits database delivery and local activation pending.
Date: 2026-10-23.

## Ownership and critical path

`workspace/lifecycle.py` uses one `cloud_workbench_begin_open` RPC for claim,
initial phase and canonical authorization facts. The RPC locks the same row and
delegates existing acquire/progress/context contracts; stale generation/deadline
cannot acquire a newer intent. Python evaluates AGENT_RUN before provider work.
This replaces three network roundtrips, not three local SQL statements.

`workspace/opening.py` observes an existing authorized intent and prepares the
API's private route while the lifecycle worker resumes its Machine. Provider
capability negotiation keeps other providers on the existing path. Preparation
does not connect SSH, access files or start compute. `checking_files` admits the
actual SSH/service handshake and root listing. Identity, generation, operation,
compute epoch, binding and resource must still match the prepared handle; stale
or failed speculation falls back to normal inspected connection establishment.
Fresh final authorization and the finish-open SQL fence remain mandatory.

`opening_wait.py` bounds observations to 128 per API process and coalesces only
the same principal/project/wait option. Last-consumer cancellation and service
shutdown cancel preparation, never the durable worker. GET `wait_seconds=0..20`
is optional (default zero); observation uses a paced one-second cadence including
RPC time. Database connections are not held while sleeping. Readiness retains its
35-second bound after the observation phase. This is bounded long polling, not a
new queue or a global permission cache; internal status SQL reads still occur.
Desktop waits immediately and only rate-limits fast pending legacy responses.

```text
Desktop explicit POST -> PG durable intent
                              |
                   worker begin_open (one RPC)
                              |
              +---------------+----------------+
              |                                |
         worker: resume VM         API status: inspect identity
              |                       prepare route + credential
              +---------------+----------------+
                              |
                      PG checking_files
                              |
                 retained SSH + service hello + root
                              |
                 current authority + SQL finish fence
                              |
                      ready + root -> Desktop
```

## Local verification

- Required backend non-integration/non-network suite: 3613 passed, 955 skipped,
  438 deselected. Initial sandbox attempt could not bind owned test sockets or
  run process checks; rerunning with local execution permissions passed. The
  intentional optional API parameter has an exact before/after contract delta;
  historical API fixtures remain unchanged.
- Additional pipeline/SSH checks: 24 passed, covering concurrent observations,
  revoked authority, operation/epoch replacement, cancellation, capacity,
  speculative failure fallback and observation deadline.
- Native PostgreSQL admission/readiness: 13 passed; populated upgrades: 2 passed.
  Existing running/paused resources and pending intent survive the upgrade.
- Authenticated HTTP + actual PostgREST/S3 local fixtures: 3 passed, including
  invalid wait bounds and proving observation alone never creates an intent.
- Database release regressions: 262 passed, 20 skipped. Manifest lint validates
  14 existing data artifacts; committed-diff policy is checked before submission.
- Desktop Cloud repository tests: 157 passed. Production TypeScript, changed-file
  ESLint and Cloud frontend architecture check passed. Readiness tests separately
  verify immediate waiting and pacing of fast old-server responses.
- Strict OpenSpec validation and changed Python lint pass.

## Measurements and release scope

The first owned real-Fly run passed creation/clone, ten warm reads, five file
channel reconnects and three actual pause/resume cycles. Resume samples were
9017.42, 9203.24 and 9042.44 ms, each with one waiting status request. Compared
with the previous same-harness median 13033.01 ms, the sample median was about
31% lower. Separate runs are not a controlled production benchmark.

The harness uses authenticated local HTTP, owned local PostgreSQL/PostgREST/S3,
real Fly Machines and separate worker/API file pools. Billing is disabled;
Desktop project resolution/rendering and hosted-database latency are excluded.
First clone is reported separately. Cold transport coverage explicitly clears
only the fixture process's connection cache; measured final samples are recorded
in `measurements.json`. Final resume samples: 10.028, 10.939, 8.980 s; cold transport resume: 10.070, 10.005, 9.875 s. Every resume completed in one waiting status request. The fixture Machines were confirmed absent and Volumes deleted (absent or pending_destroy). No existing user Machine was paused or deleted.

Read-only inspection found 56 applied Qubits migrations through 20261023030000.
The complete pending set is only 20261023040000, an additive function with no
table/column changes or data prerequisites. Hosted mutation is exclusively the
protected Qubits release workflow. New local code is activated after successful
database verification. Remote application deployment is outside this delivery.
