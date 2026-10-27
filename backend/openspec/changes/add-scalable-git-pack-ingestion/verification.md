# Byte-window ingestion and bounded metadata — implementation evidence

Date: 2026-10-22. Based on `qubits@b54cfd53`, isolated branch
`codex/git-byte-ingestion`. This is local implementation/test evidence, not
deployment, new-format acceptance or a completed five-second initialization.

## First-slice behavior (historical)

- Git's `prepare()` produces encoded objects lazily; it no longer flushes every
  100 OIDs. `StorageBackend.put_stream_durable` is the transport-independent port.
- S3 mapping and stream writes use one partitioner and `IOStorageStrategy`.
  Normal windows target 8 MiB of encoded payload, with at most four in flight
  and 32 MiB of in-flight payload. A 16,384-entry ceiling bounds metadata. One
  larger object runs alone, capped at 128 MiB of encoded bytes in this profile.
- One pending lookahead, decoding, POB headers, physical encoding/readback,
  caches and SDK memory are additional. This is not a measured 512 MiB RSS cap.
- Shared upload/readback credits prevent nested physical fanout from multiplying
  operation concurrency. The persistent bridge loop caps these requests at eight
  across backend instances; separate loops/processes have separate pools.
- Existing per-200-row capacity/location RPCs, single-attempt S3 writes, child
  leases, physical byte readback, publication fences and final ref CAS remain.
  Each write window reserves and settles a distinct I/O identity.
- Failure/cancellation drains started workers and producer threads. The bridge
  timeout also waits for outstanding work rather than detaching its owner.
- All physical formats and cold readers remain compatible. Batched single-object
  tails use POB; the explicit single-object write API still selects loose/chunked.
  No migration or GC format change is needed for this slice.

## Fixed-pack structural comparison

Command from `backend/`:

```sh
uv run python -m tests.repository_hosting.harness.ingestion_benchmark \
  --pack /path/to/incoming.pack --output /tmp/object-ingestion.json
```

The supplied full pack is 19,999,605 bytes, with 6,013 objects; the native write
path also includes the canonical empty tree. `benchmark.json` records the pack
SHA-256 and raw component results. Both modes parse the same pack, encode the
same ordered objects and execute production S3 backend code. The legacy mode
feeds the old 100-object windows; the new mode feeds one lazy stream.

| Metric | Old 100-object windows | New byte windows |
| --- | ---: | ---: |
| Capacity write windows / settlements | 61 | 5 |
| S3 PUT | 61 | 5 |
| Physical GET readback | 61 | 5 |
| Capacity reservation RPC pages | 61 | 32 |
| Object-location registration RPC pages | 61 | 32 |
| Capacity settlement RPC | 61 | 5 |
| Uploaded physical bytes | 34,094,227 | 34,099,548 |
| Cold-read object comparisons | 6,014 passed | 6,014 passed |

S3/PG are explicit in-process doubles. The fake PUT yields for 2 ms; reported
times are component overhead only. These counts exclude real child leases,
authorization, closure-proof queries/writes, ref publication and Desktop setup.
The component benchmark observed one simultaneous PUT because the fake storage
was faster than the producer; blocked-I/O tests separately prove bounded
concurrency. Smaller batch count is not a measured 12× end-to-end speedup.
POB bytes are still larger than the incoming Git pack: delta preservation is
future work, and this slice does not claim transfer-volume reduction.

## Validation

Final required backend gate:

```sh
uv run pytest -q -m 'not e2e and not integration and not network' \
  --disable-warnings --tb=short
```

Result: **3,323 passed, 885 skipped, 399 deselected**, 53 warnings, 73.62 seconds.
The local run reused the existing backend virtual environment with `--no-sync`;
it executed this worktree's source. `gate.txt` preserves the sanitized output.
Skipped/live gates are not counted as acceptance. Full Ruff checks passed for
new modules/tests and the policy module; syntax/name checks passed for the larger
modified modules. `git diff --check` and strict OpenSpec validation passed.
The sandbox initially blocked localhost test sockets; the final local suite was
run with localhost binding allowed. No hosted service was targeted.

## Outstanding architecture work after the first slice

Desktop preparation/reconciliation, byte-credit staging and larger DB pages,
streaming physical file uploads, original-pack segments/manifests/deltas,
new-format readers/GC, global tenant/process admission, live PG/S3 validation
and complete fresh-target initialization under five seconds remain unimplemented
or unverified. The original Git body/graph/object/time limits remain in force.

## Second slice: one prepared ingestion operation

The native Git adapter passes exact facts from the already parsed, bounded
incoming pack to `put_prepared_stream_durable`. `prepared_ingestion.py` owns one
fresh capacity I/O ID across all physical windows, validates the encoded stream
against the admitted inventory, accumulates physically verified placements and
settles once. `metadata_pages.py` independently caps metadata arrays at 4 MiB /
16,384 rows. Other callers with unknown inventories keep the original stream
port and its per-window identities.

The additive migration `20261022010000_expand_ingestion_metadata_pages.sql`
introduces service-role page RPCs for capacity, locations, proofs and seal. A
page runs existing checked primitives in ordered internal chunks in one atomic
transaction. Existing quotas, actor/pin/generation/GC checks, object identity,
proof dependencies and old interfaces remain. This removes network round trips;
SQL still does per-object work and holds the existing locks for a bounded page.
This is not the future COPY/staging-credit implementation.

Capacity seal still reconciles physically verified closure facts; it now uses
byte pages instead of 200-row network requests. Proof persistence retains
dependency order and its 2 MiB individual-proof ceiling. Neither optimization
removes durable closure verification or final checked ref publication.

### Fixed-pack request structure

`benchmark-metadata.json` is a rerun of the component command above, extended to
all three modes. Same pack SHA-256, source bytes and ordered object set:

| Upload metric | First byte-window slice | Prepared operation |
| --- | ---: | ---: |
| Physical write windows | 5 | 5 |
| I/O identities | 5 | 1 |
| Capacity reservation RPC | 32 | 1 |
| Location registration RPC | 32 | 1 |
| Settlement RPC | 5 | 1 |
| Total of these DB calls | 69 | 3 |
| S3 PUT / physical readback GET | 5 / 5 | 5 / 5 |
| Physical bytes written | 34,099,548 | 34,099,548 |
| Fresh-reader object comparisons | 6,014 passed | 6,014 passed |

The 95.7% reduction applies only to these upload metadata round trips. Child
leases, heartbeats, preparation, proof lookup, closure reads and final ref
publication are excluded. The corresponding capacity-seal unit test exercises
6,014 facts in one RPC, retaining empty and multi-page cases. Proof page counts
depend on their encoded edge metadata, not just object count.

### Local validation

- Component tests cover multiple physical windows sharing one I/O identity;
  byte/count metadata bounds; denial before writes; missing/extra/mismatched
  facts; corrupt readback; failed indexing; repeated cancellation; and cold reads.
- A disposable native **PostgreSQL 17.11** applied all 49 schema migrations,
  unchanged except the harness's documented three unavailable Supabase extension
  declarations in the baseline. **73 tests passed** across metadata pages,
  capacity reservations, publication pins and the real ref-transaction service.
- SQL tests exercise a bad last internal chunk rolling back a complete page;
  concurrent quota competition; idempotence; independent I/O ownership; stale
  actors/pins; namespace rejection; proof dependency ordering; service-role
  privileges; payload limits; and old interfaces. Seal rejects unsettled I/O
  and rolls back its final capacity adjustment atomically.
- A final focused component run after adding admission heartbeats passed
  **65 tests**. The required full backend gate on the final source passed:
  **3,333 passed, 901 skipped, 399 deselected**, 53 warnings, 76.74 seconds.
  `gate-metadata.txt` records the output. Skipped gates are not acceptance.
- Strict OpenSpec validation, changed-code lint (excluding the existing bridge
  `SIM105` style suggestion) and `git diff --check` pass.

SQL fixtures are control-plane evidence, not evidence of physical S3 durability.
Native PostgreSQL uses auth stubs; it does not replace real PostgREST/Supabase
authorization acceptance. These tests never targeted the running developer API
or a hosted database/object service, and owned PostgreSQL clusters were stopped.

### Rollout and remaining limits

Apply the additive migration through the normal release process **before** the
new writer starts. Missing page RPCs fail closed; no automatic fallback bypasses
the new contract. Old writers/readers retain their APIs and POB/loose/chunk
representations. No new storage format or hosted deployment occurred here.
Populated Docker release admission remains required before merge/deployment;
the fresh native PG run is supplementary evidence only.

Full fresh-target initialization under five seconds is **not yet accepted**.
Remaining work includes durable-readback proof reuse, preserving incoming pack
deltas, Desktop preparation, large-input streaming/staging credits and their
resource/recovery profiles. The earlier cancelled network benchmark was not
restarted. Component timing is deliberately not presented as an end-to-end
speed claim.


## Third slice: reuse physical evidence, bind Desktop attempts, set-based SQL

User approved items 1–3 on 2026-10-22. The deployment topology and existing
PostgreSQL refs/S3 authority remain unchanged. Git still runs in the API role;
Upload/Import/Synchronize/MCP roles, Redis and storage formats are unchanged.
This slice changes algorithms, the optional Git HTTP attempt header and SQL
function implementations. It is not configuration-only tuning.

### Delivered behavior

- Prepared S3 ingestion validates object identity/type/edges and retains bounded
  metadata only. After real physical GET verification, checked index ACK and
  settlement, it promotes placement-bound facts into the current publication
  context. Closure verification traverses these typed facts and calculates the
  same aggregates; it does not treat them as transitive published proofs. SQL
  proof registration/seal still checks the pin, generation and GC epoch.
- Evidence is cleared when its context exits, including captured contexts.
  The estimated serialized metadata budget is 16 MiB/100,000 objects; Python
  overhead is extra. Over-budget objects and byte-consuming callbacks use the
  durable reader. No global byte cache, Redis or disk repository was added.
- Desktop sends its journaled immutable UUID through the optional
  `X-PuppyOne-Initial-Attempt` header. The backend accepts it only for one
  expected-absent main creation and uses the existing checked transaction key.
  Existing authenticated operation-status GET returns the durable result;
  project, actor, attempt, ref and OID bindings remain checked. Ordinary Git
  without the header remains compatible.
- The normal first-push path removes four auxiliary remote probes and trusts
  successful Git receive-pack status. Local identity/credential checks remain.
  Lost response recovery checks the bound receipt before remote-ref fallback.
  Missing/pending results stay uncertain; reconciliation cannot create another
  attempt from absence alone. Definite rejection stops the bounded polling.
- Migration `20261022020000_optimize_ingestion_metadata_sets.sql` replaces only
  capacity/location page internals with set operations. Page bounds, lock order,
  quota identity checks, ACLs, epoch fencing, and atomic rollback remain. Proof
  page registration still uses checked dependency-ordered chunks; final CAS and
  current authorization are unchanged. Old RPCs remain available.

### Evidence and limits

`benchmark-readbacks.json` uses the same immutable full pack digest and root as
prior slices. Its fixed sorted-object write order produces 6,013 reachable
objects and 6,014 admitted/uploaded objects including the empty tree. Under
local S3/PG doubles, with identical final manifest SHA-256:

| Closure work after upload | Prior durable-reader path | Reused evidence |
|---|---:|---:|
| S3 Range GETs | 6,003 | 0 |
| Location queries | 5,318 | 0 |
| Missing-proof queries | 2,865 | 0 |
| Component closure seconds | 1.6703 | 0.6257 |

Both paths retain 5 PUTs and 5 physical upload readbacks. The evidence inventory
uses 5,071,948 estimated serialized metadata bytes. These are actual production
control-path call counts against substituted transports, not measurements of a
hosted request or a universal count for arbitrary packing/history. Component
seconds exclude network latency, Desktop, final SQL publication and other
admission work. A shared digest proves equivalent graph results for this input;
it is not full end-to-end acceptance.

- Required backend gate: **3,348 passed, 902 skipped, 399 deselected**, 53 warnings,
  75.45 seconds (`gate-third-slice.txt`). Skips do not count as acceptance.
- Added real stock Git HTTP header propagation check plus receipt lifetime,
  namespace, graph budget, missing-child and failed-write tests: **16 passed**
  (`gate-receipt-focused.txt`). This final focused run includes the one HTTP
  test added after the full gate; production behavior was unchanged.
- Fresh owned PostgreSQL 17.11 with all 50 migrations: **74 passed**
  (`sql-sets.txt`), including 6,014-object set admission, one capacity event,
  identity mismatch rollback, competing quotas, expired pins, page limits,
  old interfaces and real ref transaction tests. Native PG auth stubs remain
  supplementary; no hosted service or populated Docker upgrade was tested.
- Desktop: **86 passed in 13 files** (`desktop-third-slice.txt`), including real
  local Git restart/conflict/credential tests and receipt rejection/uncertainty.
  The later validation-helper extraction passed 10 focused receipt tests.
  The complete Desktop boundary suite and changed-file ESLint passed; existing
  unused suppression notices were not used to weaken checks.
- Strict OpenSpec validation, changed-code Ruff (excluding the pre-existing
  bridge SIM105 suggestion), and diff whitespace checks pass.

Migration deployment precedes backend activation; then Desktop may use the
header/receipt path. The existing developer API was not restarted and hosted
migrations were not run. Populated Docker upgrade rehearsal remains required
before merge/release. Full initialization below five seconds remains unaccepted.
An interrupted writer that never stores a final result can still require later
operator/control-plane recovery; receipt absence is deliberately not failure.
The first two broad proposal sections (large staging credits, segmented pack,
global scheduling and cross-process crash recovery) are not completed by this
slice.


### Local delivery

The verified runtime files were synchronized with hash checks and pre-write
backups into the existing backend/Desktop `qubits` working directories. Their
unrelated user changes were preserved. Canonical Desktop documentation was
updated in its owning `puppy-issues` domain and passed its validator (25 existing
cross-repository link warnings remain). Desktop source is archived locally at
`codex/cloud-push-receipts` (`0ce715ee`); backend source and evidence are archived
on `codex/git-byte-ingestion`. These review commits are not remote pushes or
merges of the dirty integration worktrees. Disposable task worktrees may be
removed after the archive commits; original user worktrees stay in place.
