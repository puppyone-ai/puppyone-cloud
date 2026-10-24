# Native Git transport over canonical objects

This is the native full-Project transport introduced after the initial ISSUE-062
bare-hosting acceptance. Native Scope projections remain a separate requirement.
Locators and credentials follow [the Git access contract](05-git-remote-accesspoint.md).

## Authority and request flow

S3 contains canonical Git commit/tree/blob/tag bytes. PostgreSQL owns refs,
symbolic/detached HEAD, object format, generation and object-location indexes.
An object may be loose, inside a legacy POB/chunked bundle, or inside a standard
Git pack. StorageBackend resolves that physical detail. Native push now retains
the standard pack; Product writes retain their existing object/bundle layout.
Both formats share PG authority and typed closure verification.

```text
Standard Git client
  -> canonical HTTP route -> current RuntimeGrant -> bounded admission
     -> discovery: PG refs and verified tag peel metadata
     -> fetch: pinned published graph -> StorageBackend -> S3
          -> incremental pack encoding -> HTTP stream -> client
     -> receive probe: authenticated flush-only request -> empty HTTP 200
          (no pack ingestion, publication identity or ref transaction)
     -> ref updates: initial-attempt policy, when supplied
          -> stock Git index-pack in bounded pack scratch
          -> typed new-object closure -> RefTransactionService
          -> standard .pack in S3 + closure proof -> atomic PG ref CAS
          -> Git report-status from the canonical transaction result
```

The previous full-repository adapter reconstructed the published closure per
request. The current first storage slice uses pack-only scratch and a bounded
pack cache. It runs stock Git to index/validate and read packs, without copying
the complete existing repository. Incoming raw-body spooling and per-object
re-encoding have been removed. Fetch still uses the existing incremental output
encoder; cold standard-pack reads may fill/index a local pack cache.

## Components

| Component | Responsibility |
| --- | --- |
| `adapters/git/native_repository.py` | Admitted orchestration and canonical publication handoff |
| `adapters/git/native_wire.py` | Packet limits, discovery and typed `ReceiveProbe` / `ReceivePush` decoding |
| `adapters/git/native_fetch.py` | v0/v1/v2 rounds, common objects, shallow boundaries and filters |
| `adapters/git/object_reader.py` | Published reachability, pinned durable reads, bounded body cache |
| `adapters/git/object_pack.py` | Published thin-base/path checks and existing incremental output encoder |
| `storage/git_pack.py` / `git_process.py` / `git_pack_worker.py` | Stock Git pack validation, index/read operations and owned resource limits |
| `storage/git_pack_io.py` / `git_pack_cache.py` | Bounded standard-pack S3 transfer and leased immutable disk cache |
| `adapters/git/execution.py` | Admission and streaming ownership through cancellation/completion |
| `read/repository_snapshot.py` | Authoritative snapshot and read pin |
| `write_engine/ref_transaction.py` | Closure proof, current policies, publication and ref CAS |
| `storage/io_strategy.py` | Physical layout and ingestion resource policy |
| `storage/object_stream.py` | Lazy byte windows, backpressure and owned worker completion |
| `storage/io_budget.py` | Shared bridge-loop and operation-wide upload/readback credits |

Stock Git owns incoming validation and thin-pack completion. An isolated upstream
Dulwich entry scanner discovers candidate thin-base IDs without executing deltas; each is authorized by the parent
against the published snapshot before import. This bounded fallback can perform
additional decoding; no custom delta implementation remains. Neither library
owns refs or replaces PostgreSQL publication.

### HTTP requests and publication attempts

One push may issue multiple HTTP requests. Before a chunked upload, stock Git's
[`remote-curl` probe](https://github.com/git/git/blob/master/remote-curl.c)
POSTs exactly four bytes, `0000`. The same Desktop initial-attempt header can
accompany both that probe and the subsequent ref-update/pack request.

`native_wire.receive_request` produces either `ReceiveProbe` or `ReceivePush`.
Only the exact flush-only body is a probe; truncated framing, shallow-only
commands and bytes after an empty command list are rejected. A delete command
without a pack remains a real ref update. Decoding has no storage side effects.

After authentication and write-authority admission, orchestration answers a
probe with HTTP 200 and an empty `application/x-git-receive-pack-result` body.
It does not create a publication receipt, consume the initial-attempt identity,
open a graph snapshot, reserve storage capacity or access S3. Existing route
authentication and project admission may still consult PostgreSQL.

Only `ReceivePush` enters the initial-main constraint, pack validation, durable
storage and `RefTransactionService`. A trusted Product publication context
cannot turn a probe into successful publication. The transport acknowledgement
means only that the probe succeeded; Git report-status and the canonical PG
result establish that ref updates committed.

Regression coverage uses real stock Git with a default-buffer large pack and
with explicitly chunked requests. Isolated PostgreSQL tests assert that probes
create no objects or transactions and initial/incremental updates publish
exactly once. Small buffered packs alone do not cover this protocol boundary.

## Concurrency and safety

Client object IDs must be reachable from captured refs or retained published
history. Mere S3 existence or a rejected publication receipt grants no access.
Raw-OID lazy fetch and external thin-pack bases obey the same rule. Structural
reachability searches do not download unrelated blobs.

Read pins remain held through streamed responses and push publication. A
cancelled response joins outstanding storage work before releasing its pin and
worker slot. Incoming checksum, sizes, count, delta bounds, object structure and
typed closure are checked before publication. Pack parsing cannot update refs.
Atomic pushes use one canonical transaction. Unknown SQL acknowledgments recover
using the original request identity and never invent a negative ref report.

Workers share immutable S3 objects and rebuildable local pack indexes. Temporary
bare directories contain packs/indexes, not authoritative mutable refs. PostgreSQL CAS still resolves competing ref changes. S3
throughput, CPU budgets, ref contention and admission remain real constraints.

### Interrupted pushes and retries

Git upload bytes are not a resumable session. A failed request may retransmit
its pack; byte-window ingestion remains a throughput/memory policy, not an
upload checkpoint contract. First and subsequent pushes both use the existing
`RefTransactionService` and checked SQL. Objects/closure must be durable before
refs, final result, audit and ref events are committed atomically in PostgreSQL.
No database transaction spans the network upload, and no new task/schema is
required for retrying a Git push.

After a missing response, inspect the bound result when available, then current
refs. An absent result is unknown, not proof that the original worker stopped.
An explicit retry of the same expected-old/desired-new ref update may use a new
request key and independent publication pin. Final CAS arbitrates overlapping
attempts; a late writer cannot overwrite a changed ref. If another attempt
already published the exact target OID, the client can report the desired state
as satisfied even if its latest physical attempt was rejected. Different remote
content requires conflict handling, never an unconditional force.

Desktop freezes the candidate across uncertain retries, including app restart
and a locally advanced source branch. It reuses Project, credential and remote
setup, exposes Retry, and performs one bounded outcome check per failure. A
rejection receipt for one physical attempt does not prove that an earlier
attempt failed. Ordinary Git clients simply rediscover refs and push again.
Unpublished objects retain existing pin/capacity/GC ownership; a client failure
does not authorize Project deletion or immediate physical object deletion.

`tests/repository_hosting/integration/test_interrupted_push_retry.py` verifies
initial/incremental failure, partial durable object writes, lost responses,
overlapping retries and cancellation against real isolated PostgreSQL with disk
objects. Stock Git HTTP cases truncate the first incoming pack, then verify a
fresh complete pack succeeds, published history survives, and a cold clone passes
`git fsck`. The focused `push-retry` PR job runs these contracts with
`--native-pg --target`, so skipped tests cannot pass that job. It is not
S3/deployment or end-to-end latency evidence.

### Standard pack ingestion (2026-10-22)

Native receive calls `put_git_pack_durable`. Stock `index-pack --fix-thin
--fsck-objects` validates and completes the pack; `show-index` reads positions
without a second full `verify-pack` scan. `cat-file --batch` supplies one bounded
object at a time for typed closure/identity checks. A complete non-thin pack is
persisted byte-for-byte. Thin completion adds authorized missing bases.

S3 uses a single PUT up to 8 MiB, otherwise multipart with 8 MiB parts and at most
four concurrent parts, also sharing existing operation/process I/O credits.
Parts are compressed-byte windows and may cross object boundaries. The S3 key
ends in `.pack`, not `.pob`. Capacity metadata pages precede physical writes;
streamed physical checksum verification, location registration and I/O settlement
precede promoted readback evidence and PG ref publication. Non-atomic multi-ref
pushes upload the pack once while retaining the outer read pin. Later transactions
perform their own closure/authority checks. Native Git preparation does not
synthesize a separate empty-tree write or reservation: a referenced empty tree
must be present in the incoming pack or the verified existing graph. Missing
dependencies fail closure verification. Product callers retain their existing
empty-tree materialization. Metadata paging and publication still require
multiple database calls; this is not a one-request publication protocol.

The generic `put_stream_durable`/`put_prepared_stream_durable` byte-window/POB
implementation remains for Product/internal callers and old data. It is no longer
the native push persistence path. Legacy object reads still return loose bytes;
removing their per-request encode/decode, pack-level PG cataloging, full warm
repository caches and pipelined HTTP reception/upload are further work.

Native receive uses `GIT_MAX_RECEIVE_PACK_BYTES` (default/hard maximum 2 GiB) and any tighter
plan limit. Initial Desktop pushes, ordinary first pushes and subsequent pushes
all use the same receiver; the initial-attempt header does not change admission.
Oversize rejection precedes object persistence/ref publication. There is no hidden 128 MiB cap. Completed packs/cold reads instead use
`GIT_MAX_STORED_PACK_BYTES` (default 4 GiB), independently of new-upload policy.
`GIT_PACK_SCRATCH_BYTES` defaults to 9 GiB per pack workspace. Startup validates
positive, consistent settings. These configured ceilings are not maximum-size
capacity acceptance. Limits remain 64 MiB per object, 100,000 objects and 300
seconds of owned work. Linux helpers also enforce address-space, CPU, file and FD
limits; macOS does not provide the same address-space enforcement. Four cached
packs per API process have leases that prevent active eviction. These independent
bounds are not a promise that total process memory equals the multipart window.

`GIT_MAX_EXPANDED_BYTES` now bounds logical read/verification work, with default
8 GiB and a finite configurable lower value. Native readers, the production
publication factory and projection reads share it. It does not allocate an 8 GiB
buffer or expand 8 GiB to disk. The pack stays compressed; per-object/cache/scratch
bounds remain separate. An explicitly configured lower environment value still
applies. Projection output and other existing product limits remain independent.

Migration `20261022030000_expand_standard_git_pack_locations.sql` permits `.pack`
keys in both existing registration RPCs without changing rows, tables, ACLs or
publication fencing. Apply Expand before activating new-format writes. All serving
readers must support `.pack` before reopening writes (reader-first release or
controlled write drain/cutover). After a pack is published, an application rollback
must retain that reader. The current change has not been deployed.

`metadata_pages.py` bounds each JSON array to 4 MiB and 16,384 entries, including
JSON separators. One outgoing page and the bounded physical workers' placement
rows may coexist with the accumulator; this is not a 4 MiB total-RAM promise.
The compatibility object inventory remains bounded by the 100,000-object incoming
profile. Capacity reconciliation/seal and dependency-ordered proof registration
use the same page policy. No Redis or separate server repository is involved.

Migration `20261022010000_expand_ingestion_metadata_pages.sql` adds service-role
page RPCs. `20261022020000_optimize_ingestion_metadata_sets.sql` implements
capacity and location pages with set-based validation, identity joins and bulk
updates. One page is one atomic transaction and one capacity event, retaining
lock order, quota/identity checks and pin/epoch fencing. Database work remains
proportional to objects; proof registration retains dependency-ordered checked
chunks. Prior interfaces remain for old writers. Apply migrations before
deploying the new backend; missing capabilities fail closed.

### Capacity transaction ownership

Migration `20261023000000_batch_capacity_authorization.sql` makes page admission
explicit. Both the compatibility 200-object RPC and the 4 MiB / 16,384-object
page RPC converge on `reserve_version_object_capacity_page`. One page checks
the current actor/file policy once, then validates and inserts the object set.
For an ordinary Runtime Git credential, credential resolution and canonical
Project role resolution each occur once, regardless of the page's object count.
Larger inventories use additional bounded pages, each with fresh authorization.

```mermaid
sequenceDiagram
    participant Git as Git / Product adapter
    participant VE as Version Engine storage
    participant PG as PostgreSQL
    participant S3 as S3 immutable packs
    Git->>VE: Verified pack and bounded object facts
    VE->>PG: reserve_version_object_capacity_page
    Note over PG: Short transaction: lock current facts, authorize once, bulk validate and reserve
    Note over PG: Recheck expiry and execution fence; commit all or roll back all
    PG-->>VE: Reservation and independent I/O identity
    VE->>S3: Upload and verify pack outside DB transaction
    VE->>PG: Register locations and settle this I/O
    Git->>PG: Final publication through RefTransactionService
    Note over PG: Fresh permission/policy checks, expected-ref CAS, atomic result and audit
    PG-->>Git: Committed or rejected transaction result
```

The private `_version_begin_capacity_admission` and
`_version_finish_capacity_admission` helpers belong to the SQL transaction, not
the Git router or the S3 client. Membership, surface, credential, policy and
lease rows remain locked within that short transaction. A local PL/pgSQL value
captures the file limit and locked time-based deadlines. The final check uses
wall-clock expiry plus the existing lease/Agent execution fence; no credential
cache or user-supplied authorization context is accepted. No transaction/row
lock spans the S3 upload. Final publication obtains current authority again.

The per-object authorization triggers are removed in the same transactional
migration. Table DML and private helpers remain inaccessible to application
roles. Owner-only inventory activation runs before policy enrollment; recovery
finalization still requires its verified inventory and remains owner-only.
A statement trigger retains the 64 MiB object guard for all actual insertions;
the runtime page also checks mixed old/new conflict inputs. PK/FK constraints,
identity mismatch checks, quota accounting, expiry rollback and separate I/O
settlement identities remain intact. Existing oversized files are grandfathered
against later plan reductions, as before.

Source ownership after this change:

```text
supabase/migrations/
  20261023000000_batch_capacity_authorization.sql  # atomic admission, bulk SQL, ACLs
backend/src/version_engine/
  storage/metadata_pages.py                     # bounded metadata payloads
  storage/backends/s3.py                        # reserve -> transfer -> verify -> settle
  infrastructure/supabase/capacity_repository.py # capacity RPC adapter and seal
  write_engine/ref_transaction.py               # final checked publication
backend/tests/repository_hosting/
  integration/test_capacity_batch_admission.py  # permissions, expiry, atomicity, call counts
  integration/test_capacity_batch_upgrade.py    # populated upgrade and rollback
  harness/capacity_benchmark.py                 # before/after full RPC in owned PG
```

The [verification record](../../backend/openspec/changes/batch-capacity-authorization/verification.md)
separates local SQL evidence from hosted activation and full push latency.

Prepared S3 writes collect bounded typed facts in `readback_receipts.py`. Facts
are promoted only after physical readback, checked placement registration and
settlement. They record the exact immutable placement and contain no body bytes.
The owning publication context clears them on exit, including copied contexts;
its pin binds generation and GC epoch, revalidated in the proof/seal SQL. The
collector caps estimated serialized metadata (including placement allowance) at
16 MiB and 100,000 objects; Python object overhead is additional. Overflow uses
the existing durable reader. Closure verification reuses these facts for new
objects but still checks typed edges, graph budgets and logical size aggregates.
Old published boundaries use retained proofs. Byte-consuming callbacks retain
physical reads. No global byte cache or PUT acknowledgement becomes evidence.

Desktop initial-main creation may send `X-PuppyOne-Initial-Attempt` containing
its immutable UUID. Only a single expected-absent main creation accepts this
extension. The UUID is the actor/project-scoped ref request key, so the existing
authenticated `/git/{project_id}.git/operations/{request_key}` endpoint returns
the durable result. The checked SQL digest still rejects changed inputs.
Ordinary Git clients remain compatible without the header. Receipt absence is
uncertain; explicit retries preserve the candidate and use CAS as described
above, without claiming that the original execution failed or stopped.
Successful receive-pack acknowledgement avoids extra Desktop `ls-remote`
probes during setup, push and accepted local finalization.

Child write leases, pin/epoch fencing and final closure/CAS checks remain in
force. On producer failure, worker failure,
cancellation or bridge timeout, started work joins before its owner exits.
Rejected/uncertain I/O is not settled as successful. This does not add a new
worker-crash recovery protocol; existing durable fences still own that case.

The earlier byte-window stage retained POB conversion. Native push now follows
standard-pack ingestion above. Generic Product/internal writers and historical
loose/POB/chunked data remain compatible; no historical repack has been run.

## Resource contract and tradeoffs

- Per-process admission uses `GIT_MAX_CONCURRENT_OPERATIONS` (positive integer,
  default `2`), read at process startup. Restart workers to apply a change.
  The health response reports that same initialized limit. Saturation returns
  `503` with `Retry-After: 1`; there is no unbounded wait queue. Streaming responses
  retain their slot until closed, and cancelled work finishes cleanup before its
  slot is released.
- Process and replica counts multiply the configured slots, not available
  hardware: `WEB_CONCURRENCY=2` with `GIT_MAX_CONCURRENT_OPERATIONS=4` gives eight
  slots per API replica. Size aggregate CPU, RAM, HTTP spools and pack scratch
  accordingly, then measure under representative concurrent pushes/fetches.
  Per-process routing can still reject work while another process has room;
  this is not a cluster-wide scheduler or a throughput guarantee. Raising this
  limit does not change S3 I/O budgets or add a reusable disk repository service.
- HTTP may spool a fetch negotiation request capped at 16 MiB. The fetch encoder
  does not spool its output; standard-pack cold reads use bounded storage scratch.
- Native pack input uses the configured deployment/plan allowance (default/hard maximum 2 GiB).
  The HTTP spool additionally allows 16 MiB of protocol commands, checked separately
  by the parser; the exact pack allowance is enforced after command parsing.
  Oversized HTTP bodies return 413 with a byte limit and clean up partial spools.
  Completed packs/cold reads use the separate storage profile (default 4 GiB).
  A thin pack may grow past its input allowance without violating input policy.
  Scratch is checked independently (default 9 GiB); startup requires space for
  two stored packs plus 64 MiB metadata headroom. Do not reduce stored-pack limits
  below published data. Reducing receive policy alone never changes cold reads.
  Logical graph work defaults to 8 GiB through `GIT_MAX_EXPANDED_BYTES`; it is not
  a decoded spool. Explicit lower settings and per-object limits still apply.
  Provision input, cold-download staging, per-operation scratch and the bounded process pack cache
  together; a helper's disk limit is not an aggregate deployment disk reservation.
  The retained legacy subprocess profile keeps its separate original limits.
- Limits remain 64 MiB per object, 100,000 objects, 10,000
  refs and 300 seconds. The object-body cache is capped at 8 MiB per request.
- Output packs compress individual objects without delta optimization. Fetch
  omits known common trees/blobs, but bandwidth can exceed stock Git's packs.
  A future bounded delta encoder requires no local repository.
- Partial-clone filters support `blob:none`, `blob:limit`, `tree` and combinations;
  unsupported selectors are rejected explicitly.
- Publication checks new dependencies and stops at retained typed proofs for
  published history. Request-local readback evidence avoids rereading freshly
  verified uploads; objects without either evidence still use physical reads.

The protocol follows Git's [pack protocol](https://git-scm.com/docs/gitprotocol-pack),
[protocol v2](https://git-scm.com/docs/protocol-v2) and
[pack format](https://git-scm.com/docs/pack-format).

## Verification

Pack tests compare SHA-1/SHA-256 objects and standard pack bytes, cover ordinary,
thin and forward deltas, empty ref-only pushes, corrupt streams, cold reads,
multiple refs, multipart failures and repeated cancellation. A 576 MiB logical
fixture retains less than 1 MiB of pack bytes and can be read without a cumulative
raw spool. Existing fetch protocol tests with substituted loose-object storage
still forbid fetch-local files/processes; they do not characterize the new cold
pack cache. Real PG tests cover initial/incremental interrupted retries and
populated migration/ACL/fencing. Real S3 acceptance remains separate.

Current first-slice evidence and deployment limits are recorded in
`backend/openspec/changes/adopt-standard-git-pack-storage/verification.md`.

Receipts and remaining limits are recorded in
`backend/openspec/changes/remove-native-git-materialization/verification.md`.

Byte-window tests additionally cover backpressure, byte/count ceilings, oversized
objects, copied publication context, concurrent capacity identities, corrupt
readback, repeated cancellation and bridge timeout. The reproducible local
component benchmark is
`python -m tests.repository_hosting.harness.ingestion_benchmark --pack input.pack`.
Its substituted storage/control plane measures request structure, not cloud
latency or the full <5-second initialization target. See
`backend/openspec/changes/add-scalable-git-pack-ingestion/verification.md`.
