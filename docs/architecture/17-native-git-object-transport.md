# Native Git transport over canonical objects

This is the native full-Project transport introduced after the initial ISSUE-062
bare-hosting acceptance. Native Scope projections remain a separate requirement.
Locators and credentials follow [the Git access contract](05-git-remote-accesspoint.md).

## Authority and request flow

S3 contains canonical Git commit/tree/blob/tag bytes. PostgreSQL owns refs,
symbolic/detached HEAD, object format, generation and object-location indexes.
An object may be loose in S3 or a byte range inside an immutable storage bundle.
StorageBackend resolves that physical detail; transport uses object IDs and
typed graph edges. Git wire packs and S3 storage bundles are different formats.

```text
Standard Git client
  -> canonical HTTP route -> current RuntimeGrant -> bounded admission
     -> discovery: PG refs and verified tag peel metadata
     -> fetch: pinned published graph -> StorageBackend -> S3
          -> incremental pack encoding -> HTTP stream -> client
     -> push: bounded incoming pack/delta quarantine
          -> typed new-object closure -> RefTransactionService
          -> durable S3 writes + closure proof -> atomic PG ref CAS
          -> Git report-status from the canonical transaction result
```

The previous adapter reconstructed the published closure in a temporary bare
repository for stock upload-pack/receive-pack, multiplying disk use by active
requests. Native transport now creates no bare repository, writes no existing
repository graph to disk, runs no Git subprocess and buffers no complete output
pack. A small request LRU can be disabled or evicted without affecting correctness.

## Components

| Component | Responsibility |
| --- | --- |
| `adapters/git/native_repository.py` | Admitted orchestration and canonical publication handoff |
| `adapters/git/native_wire.py` | Packet limits, byte-preserving discovery and receive commands |
| `adapters/git/native_fetch.py` | v0/v1/v2 rounds, common objects, shallow boundaries and filters |
| `adapters/git/object_reader.py` | Published reachability, pinned durable reads, bounded body cache |
| `adapters/git/object_pack.py` | Bounded incoming pack/delta codec and incremental output encoder |
| `adapters/git/execution.py` | Admission and streaming ownership through cancellation/completion |
| `read/repository_snapshot.py` | Authoritative snapshot and read pin |
| `write_engine/ref_transaction.py` | Closure proof, current policies, publication and ref CAS |
| `storage/io_strategy.py` | Physical layout and ingestion resource policy |
| `storage/object_stream.py` | Lazy byte windows, backpressure and owned worker completion |
| `storage/io_budget.py` | Shared bridge-loop and operation-wide upload/readback credits |

Dulwich is pinned as an object-validation/delta codec inside transport. It owns
neither refs nor storage and does not replace the Version Engine. The adapter
does not instantiate a Dulwich Repo or MemoryObjectStore.

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

Workers share immutable S3 objects without a writable repository directory or
filesystem ref locks. PostgreSQL CAS still resolves competing ref changes. S3
throughput, CPU budgets, ref contention and admission remain real constraints.

### Byte-window ingestion (2026-10-22)

Git receive passes a lazy encoded-object stream to `StorageBackend.put_stream_durable`.
Transport no longer decides a 100-object write boundary. Existing mapping writes
(`async_put_many`) use the same storage ingestion path. `IOStorageStrategy` owns
the defaults: 8 MiB encoded payload per window, 4 in-flight windows and 32 MiB
in-flight encoded payload. A window may also close at 16,384 entries to bound
metadata. One larger object runs exclusively, with a separate 128 MiB encoded
object ceiling, and uses the existing chunked-object representation. These are
finite configurable ingestion limits, not new permission to exceed Git/plan caps.

The producer has at most one batch of lookahead for byte admission. Payload
credits exclude that lookahead, decoding workspaces, POB headers, physical
encoding copies, readback buffers, caches and SDK overhead; they are not a total
process RAM claim. POB's payload target also excludes its bounded JSON header.
Uploads and readbacks share four operation credits across nested batches. S3
backend instances using the persistent bridge loop share eight physical I/O
credits; other event loops have separate pools. This is not a cross-process or
tenant-wide scheduler. Limits belong to policy, not to the generic S3 driver.

For a parsed native Git pack, `put_prepared_stream_durable` admits exact kind/size
facts for the whole operation before any PUT. Every byte window shares one fresh
I/O ID. `prepared_ingestion.py` checks streamed objects against that inventory,
collects only physically read-back placements, flushes independent metadata
pages and settles once after all workers and index acknowledgements finish.
Unknown-inventory callers retain the per-window `put_stream_durable` path.

`metadata_pages.py` bounds each JSON array to 4 MiB and 16,384 entries, including
JSON separators. One outgoing page and the bounded physical workers' placement
rows may coexist with the accumulator; this is not a 4 MiB total-RAM promise.
The prepared inventory remains bounded by the existing 100,000-object incoming
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
uncertain; it is never permission to automatically start another attempt.
Successful receive-pack acknowledgement avoids extra Desktop `ls-remote`
probes during setup, push and accepted local finalization.

Child write leases, pin/epoch fencing and final closure/CAS checks remain in
force. On producer failure, worker failure,
cancellation or bridge timeout, started work joins before its owner exits.
Rejected/uncertain I/O is not settled as successful. This does not add a new
worker-crash recovery protocol; existing durable fences still own that case.

This first stage retains POB/chunk/loose formats, existing readers and GC. It
still expands incoming Git deltas and re-encodes objects. Preserving an incoming
Git pack in 64 MiB compressed segments requires the separate manifest, reader,
proof and GC work described in `add-scalable-git-pack-ingestion`; it is not
enabled by this change.

## Resource contract and tradeoffs

- Per-process admission remains two Git operations; this does not claim 1000
  simultaneous transfers per process.
- Fetch repository/output scratch is zero. HTTP may spool a negotiation request
  capped at 16 MiB.
- Push input is capped at 128 MiB or a tighter deployment limit. Its incoming
  object/delta spool is capped at 256 MiB and contains no downloaded old graph.
- Limits remain 64 MiB per object, 256 MiB graph bodies, 100,000 objects, 10,000
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

Component tests forbid temporary files and Git subprocesses during cold fetch,
compare both object formats against stock Git, exercise eight concurrent readers,
prove incremental/filtered reads omit unnecessary blobs, and cover cancellation,
unpublished-object denial and malformed packs. HTTP tests cross three protocol
versions with both formats. Real PG/S3 and authenticated application acceptance
are separate layers; component substitutes are not deployment evidence.

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
