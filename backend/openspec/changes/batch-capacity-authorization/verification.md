# Batch capacity authorization verification

> Status: Evidence
> Owner: Puppyone Cloud Version Engine
> Date: 2026-10-23
> Source: `puppyone-cloud@af7d53b47771292da6cac3a8cebf842406683494` plus the local candidate
> Release state: merged to qubits; hosted SQL migration verified; full push acceptance pending

## Measured complete capacity transaction

The benchmark uses an owned disposable PostgreSQL 17 cluster, the product
migrations, an enrolled Runtime Git credential, file policy and quota accounting.
It applies the same 6,017 object facts from the reported upload to the pre-change
and post-change RPC. Each measurement rolls back its writes. No developer
database URL, Supabase credentials or S3 endpoint is accepted by the benchmark.

Input: 19,994,608 compressed pack bytes, 6,017 Git objects,
63,081,993 aggregate object-body bytes. The pack SHA-256 and individual function
statistics are in [capacity-benchmark.json](capacity-benchmark.json).

| Complete reservation RPC | Execution time |
| --- | ---: |
| Before migration | 20,590.884 ms |
| After, run 1 | 171.494 ms |
| After, run 2 | 176.629 ms |
| After, run 3 | 173.124 ms |
| After median | 173.124 ms |

The measured median improvement is approximately 119x. These numbers include
metadata validation, current authorization, capacity/inflight bulk inserts,
foreign keys, counters and the allocation event. They exclude client pack
creation, HTTP/DB network latency, S3 writes/readback and final ref publication.
They are not a hosted subsecond guarantee or five-second full-push acceptance.

| Function work for one new page | Before | After |
| --- | ---: | ---: |
| Git credential resolution | 24,068 | 1 |
| Project role resolution | 48,136 | 1 |
| File policy lookup | 12,034 | 1 |
| Write lease/execution fence | 24,068 | 2 |
| Per-object authorization trigger invocations | 12,034 | 0 |

## Regression contracts

The focused suite covers 1, 6,017 and maximum-page 16,384 objects, the old
200-object RPC, repeated I/O identities, metadata-only duplicate reconciliation,
revocation serialization, expiry after admission (credential, lease, entitlement,
pin), tail size rejection, grandfathered existing objects, 64 MiB object guards,
private helper/table ACLs and verified owner-only archive compatibility.

The populated upgrade test installs the preceding schema, allocates existing
objects, injects a late migration failure and verifies functions/triggers/grants
and all relevant rows are unchanged. Applying the unmodified migration preserves
data and old RPCs, including the default fresh I/O identity. Both old and new
interfaces reject a subsequently revoked writer.

The SQL change has no new application table or data backfill. The new migration
must be released before claiming the hosted issue is fixed. Restarting Python
alone cannot update these PostgreSQL functions/triggers. Old application code
can use the replacement RPCs without a coordinated application protocol change.

## Reproduction

From the code repository root, with installed PostgreSQL 17 binaries on PATH:

```sh
python3 scripts/testing/run_repository_hosting.py --native-pg --target \
  --output /tmp/puppy-capacity-batch-verified -q \
  -k '(test_capacity_batch_admission or test_capacity_batch_upgrade or test_repository_write_admission or test_repository_file_policy or test_ingestion_metadata_pages or test_repository_capacity) and not test_supabase_data_api'
```

From `backend`, supplying a locally available complete SHA-1 pack:

```sh
.venv/bin/python -m tests.repository_hosting.harness.capacity_benchmark \
  --pack /path/to/source.pack --output /tmp/capacity-benchmark.json
.venv/bin/python -m src.infra.data_migrations lint
.venv/bin/python -m pytest tests/infra/data_migrations/test_schema_security.py \
  tests/infra/data_migrations/test_policy.py -q
```

The broad supplementary native-PG run reported 1,239 passed, 500 skipped and
29 deselected. Its runner exited 1 because required real Auth/PostgREST/S3 cases
were skipped; it is not a passed full acceptance gate. The focused no-skip gate
passed 124 tests with zero skips (1,665 deselected); the runner exited 0.
This includes the populated upgrade and a 16,384-object maximum page. Schema security/policy
tests passed 14 cases; migration artifact lint validated 14 existing data artifacts.

## Qubits release admission

The target was refreshed to `3032904e9c2d554bc02b77ed8171d7ef0aaba98c`.
A read-only audit found 52 applied migrations through `20261022040000`.
Only `20261023000000` is pending; there are no older missing or unknown applied
versions. All 52 historical migration files are unchanged, and all 53 candidate
SQL checksums match the locally verified schema. This is a compatible function
and trigger replacement with no new table, data rewrite or Contract prerequisite.
The isolated release candidate on that refreshed base also passed the same
focused suite: 124 passed, 1,650 deselected, zero skipped, runner exit 0
(84.16 seconds). Schema security/policy checks passed 14 tests.
Hosted activation completed through [PR #1405](https://github.com/puppyone-ai/puppyone-cloud/pull/1405),
merge `b2b84e97ed92f2ea581a9adc52e8199a50635c2f`.
[Database release 39821341448](https://github.com/puppyone-ai/puppyone-cloud/actions/runs/39821341448)
completed successfully, including migration and verification. Application deployment
was explicitly skipped. A separate read-only audit confirmed all 53 migrations,
no pending versions, zero old per-object authorization triggers, one statement
object-budget trigger and three private helpers with no application-role execute
privilege. Local backend `/health` returned HTTP 200. Full push acceptance remains
unmeasured after this migration.

## Remaining work outside this slice

- Measure a successful real upload after migration, including S3 and final publication.
- Consolidate redundant route/discovery DB requests.
- Distinguish known pre-publication failures from lost final-commit acknowledgements.

No upload continuation, new microservice, Redis cache or longer global database
timeout is introduced as a substitute for batching.
