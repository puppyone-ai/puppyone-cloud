# Verification — standard Git pack first slice

Date: 2026-10-22. Candidate: Git pack and interrupted-initialization fixes, based
on refreshed `qubits` commit `af7d53b47771292da6cac3a8cebf842406683494`.
The results below are local verification, not hosted release evidence. The PR
and its GitHub Actions runs record subsequent merge and release status.

Release preparation rechecked all 50 applied Qubits migration versions against
the 52 candidate SQL files: only `20261022030000` (pack locations) and
`20261022040000` (native initialization abandonment) are pending. There are no
older missing versions or applied versions absent from the checkout. Both
replace existing functions and preserve ACLs; neither rewrites stored data.

The final abandonment suite passes 13 real-PostgreSQL cases, including populated
upgrade, rollback, rejected publication receipts, idempotent deletion, retained
capacity during drain, unknown dependents, and push/discard races. The latest
full backend regression passes 3,446 cases (926 skipped, 400 deselected); the
subsequent SQL-only refinement passes the complete 13-case abandonment suite.
Related publication, capacity and location integration tests pass 89 cases
before that final rejected-receipt refinement. The final Desktop coordinator
passes 41 cases; journal/UI follow-up passes 37. These do not establish hosted
application deployment or maximum-size/performance acceptance.

## Results

| Layer | Result | Meaning |
| --- | --- | --- |
| Last full backend regression | 3,444 passed; 914 skipped; 400 deselected | Fixed 2 GiB production code, 133.53 seconds; subsequent follow-up changes tests/docs only; skipped live/provider tests are not acceptance |
| Pack limits, protocol and security suites | 84 passed; 1 skipped | Latest fixed-ceiling candidate; actual Git, SHA-1/SHA-256, thin completion, original bytes, cold reads, rejection/cleanup, policy and metadata limits; Linux rlimit acceptance remains skipped on macOS |
| Desktop push/retry/UI | 98 passed; 46 relevant cases rerun after final copy changes | Rejection with disconnect text or unavailable status, confirmed-success precedence, one push invocation; eight locales, UI snapshots, localization/vocabulary/architecture checks and focused ESLint passed |
| Isolated PostgreSQL suite | 78 passed; 1,659 deselected | Latest run, 34.94 seconds: actual migration SQL, populated upgrade/rollback, ACL/data/fences, ref atomicity, retry, and oversized first/incremental pushes |
| Shared HTTP admission follow-up | 4 passed; 20 deselected | Initial-attempt header present/absent: identical cap forwarding and declared-oversize rejection before streaming |
| Earlier expanded hosting regression with isolated PostgreSQL | 1,186 passed; 500 skipped; 29 deselected | Before the configurable-limit follow-up: 398 `hosting_live` cases passed against PostgreSQL 17 with auth stubs; not a full rerun of the latest candidate |
| New OpenSpec delta | Strict validation passed | Scoped requirements and implementation boundary recorded |
| Focused source lint / whitespace | Passed | Newly added modules and affected transport/factory/projection tests checked; `git diff --check` clean |
| Documentation governance | Passed | 433 Markdown files; 25 pre-existing unresolved cross-repository link warnings |
| Workflow configuration | YAML parsed | New PR job defined for owned real S3/PostgREST/Linux; not an executed CI result |
| Real S3/PostgREST acceptance | BLOCKED before tests | Owned Supabase database container remained `created` for 60 seconds; independent no-network `/bin/true` container also failed to start within 20 seconds |
| Hosted deployment / <5-second target | Not performed / not established | Local component timing must not be represented as user upload latency |

The synthetic large fixture has 18 distinct 32 MiB bodies (576 MiB logical), a
compressed pack below 1 MiB, no raw object spool, and successful subsequent
published-reader body reads. This proves separation of logical work from scratch;
it does not establish the performance of the user's repository or S3.

A fresh physical proof rejects corrupted S3-double bytes despite an earlier warm
cache. Upload/readback/index/settlement faults promote no proof. Multipart tests
verify byte bounds, shared I/O credits, part completion before abort, and repeated
cancellation. Real PG retry tests truncate a stock Git upload, retransmit a fresh
complete pack and verify published history/cold clone. Network cable removal was
not required.

Follow-up on 2026-10-22: the expanded hosting run completed in 333.46 seconds
(test-suite runtime, not push latency). Its 500 skips comprise 191 S3 cases,
181 application cases, 127 Auth/PostgREST cases and one Linux resource-limit
case. Supabase SQL suites and the object service were not started in this
profile. Docker Desktop returned its version and existing-container resource
statistics, but a new no-network `/bin/true` probe still timed out after 20
seconds; the owned probe was removed successfully. This establishes the test
startup blocker, not its underlying Docker cause or S3/application acceptance.

The configurable-limit follow-up passed the required backend regression in
147.57 seconds and reran the selected real-PG suite (75 passed) in 35.75 seconds.
A real incompressible pack containing three distinct 44 MiB blobs crosses the
former 128 MiB receive ceiling, persists, and cold-reads all bodies correctly
after the receive allowance is reduced to 1 KiB. A separate S3-adapter test uses
a storage double to prove cold reads after a receive-policy reduction to one
byte. Thin-pack tests accept an exact wire allowance while the completed pack
grows larger, then independently reject an insufficient completed-pack budget.
HTTP tests verify 413 cleanup and propagation of the plan cap past the separate
command allowance. Startup validation rejects inconsistent resource profiles.
These earlier tests did not establish maximum-size end-to-end capacity or deployment-wide disk
admission. No additional SQL migration was introduced by this follow-up.

The fixed-ceiling follow-up ran the 84 passing Git/security cases in 68.27 seconds.
A scaled real-pack test accepts the exact byte limit and rejects a one-byte
oversize with scratch cleanup. Numeric configuration tests accept 2,147,483,648
and reject one additional byte; HTTP admission rejects an oversized declared
length before streaming. These tests are not a real 2 GiB capacity benchmark.
Stock Git still sends once; no extra client packing or automatic multi-push
splitting was introduced. There is no new SQL migration in this follow-up.
Initial sandboxed test attempts could not bind localhost or inspect subprocesses;
both the focused and full backend suites passed when rerun with local process/network permissions. The initial
Desktop snapshot mismatch was the intended wording change; final snapshots pass.

The all-push follow-up adds actual stock-Git/HTTP/PostgreSQL cases for an
ordinary first push, Desktop-style first push with its attempt header, and an
incremental push. A deliberately small receive budget exercises the real pack
rejection path. Each case verifies unchanged refs/ref sequence, transaction
count, durable object set and local working files, plus exactly one receive and
removal of its request spool. The selected 78-case PG suite passed. Four focused
HTTP cases also passed. No runtime implementation or schema change was necessary
because all push types already enter the same admission and pack parser.

## Reproduce

From `backend/`:

```sh
NOTIFICATIONS_REDIS_URL='' uv run pytest -q -m 'not e2e and not integration and not network' --disable-warnings

NOTIFICATIONS_REDIS_URL='' uv run pytest -q --disable-warnings \
  tests/repository_hosting/unit/test_standard_git_pack.py \
  tests/repository_hosting/unit/test_direct_object_transport.py \
  tests/repository_hosting/unit/test_native_size_limits.py \
  tests/repository_hosting/unit/test_native_execution.py \
  tests/security/test_audit_git_limits.py

uv run python ../scripts/testing/run_repository_hosting.py --native-pg -q \
  --disable-warnings \
  -k 'interrupted_push_retry or ref_transaction_service or standard_git_pack_upgrade or ingestion_metadata_pages or object_location_fence'

# Expanded follow-up, including all cases supported by this isolated PG profile.
uv run python ../scripts/testing/run_repository_hosting.py --native-pg -q \
  --disable-warnings

# Requires working local Docker; never uses ambient hosted S3/PG credentials.
uv run python ../scripts/testing/run_repository_hosting.py --live --s3 --docker \
  --target -q \
  -k 'standard_git_pack or direct_object_transport or native_size_limits or test_stock_push_durable_native_refs_and_cold_clone or test_native_http_atomic_refs_tags_rewrites_and_delete or native_s3_product_writes'
```

The first broad run found three loose-filesystem instrumentation regressions and
one developer notification-Redis setting leak. The loose-only readback behavior
was preserved; the final broad run explicitly disables notification Redis and
passes. These fixes do not disable storage/PG admission or relevant test cases.

## Release boundary

- `20261022030000_expand_standard_git_pack_locations.sql` is an additive key-format
  expansion of two existing RPCs. Only isolated test databases have applied it.
- Deploy compatible readers everywhere before new-format writes; use a reader-first
  release or drain/cutover. POB-only application rollback is unsafe after pack writes.
- Nixpacks explicitly installs stock Git. Linux helper resource enforcement is
  included in the new owned S3 CI job; local macOS results are not Linux RSS evidence.
- `.github/workflows/repository-hosting-tests.yml` now includes pack/unit/migration
  cases in the existing PG PR job and a separate GitHub-hosted Docker/S3 job. No
  self-hosted runner or production credentials are required. Branch protection
  and execution of that new job have not been changed/proven by a local YAML edit.
- Current configured defaults are 2 GiB incoming pack, 4 GiB completed/cold-read
  pack and 9 GiB scratch per pack workspace. Receive configuration cannot exceed
  the product maximum of 2,147,483,648 bytes per push; it may be tightened. These are independently validated
  settings, not maximum-size acceptance. HTTP commands have a separate 16 MiB
  allowance; a tighter plan cap applies to the actual incoming pack. Keep the
  storage profile large enough for existing data when reducing upload policy.
  Limits remain 64 MiB per object, 100,000 objects and 8 GiB logical work. LFS and
  a larger single-object profile have not been implemented. This is not unlimited
  hosting or an aggregate deployment disk reservation.
- Full warm repository SSD service, pack-level PG metadata, receive/upload pipeline,
  legacy read representation removal and production performance tuning remain
  outside this first slice. See `design.md` and the canonical storage architecture.
