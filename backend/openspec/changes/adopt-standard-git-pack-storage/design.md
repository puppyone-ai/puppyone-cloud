# Standard pack first slice

## Ownership and flow

`adapters/git/object_pack.py` binds reachability and protected-path validation to
`storage/git_pack.py`. The storage layer runs stock `index-pack --fix-thin
--fsck-objects`, reads the resulting index with `show-index`, and uses a persistent
`cat-file --batch` child for object bodies. There is no custom incoming delta
interpreter, cumulative raw object spool or per-new-object compression step.
An isolated, resource-limited upstream Dulwich entry scanner discovers candidate thin
base IDs without executing deltas; the parent authorizes each base against the pinned published snapshot.
Only these existing bases are imported. Stock Git appends them to make the pack
self-contained. This thin-pack fallback performs extra validation work; it is
not an assertion of zero decoding or a complete repository cache.

S3 reserves existing capacity pages, uploads the completed pack, streams back its
checksum, registers location pages, settles I/O, then promotes publication-local
typed evidence. PG still verifies closure/policy/quota and publishes refs/results
atomically. Later refs in a non-atomic push reuse the physical pack under the
outer read pin but obtain their own publication checks. Partial/unknown outcomes
never create a successful ref report.

## Format compatibility

`version/<project>/object-bundles/<sha256-prefix>/<sha256>.pack` contains the full
completed Git pack. Existing `offset_bytes`/`size_bytes` identify its compressed
entry, not loose bytes. The suffix selects the standard reader. A bounded local
pack cache downloads a complete pack and rebuilds its native index once per fill;
subsequent reads use the index. A fresh durability attempt gets a separate cache
identity so a warm cache cannot mask missing/corrupt S3 data. Legacy object APIs
still return loose bytes, so read-side encode/decode is a known next optimization.
Whole-pack GC must retain the physical pack while any member remains live,
including bases needed by live deltas. No historical data is rewritten.

## Resource envelope

Pack policy is defined by the pure `domain/git_pack_limits.py` contract and bound
to Settings by `storage/git_pack_limits.py`. `GIT_MAX_RECEIVE_PACK_BYTES` now
controls the receiver as well as HTTP admission (default/hard maximum 2 GiB); there is no
second hidden 128 MiB cap. A tighter plan allowance reaches the actual pack
parser. Protocol commands have a separate 16 MiB bound; their allowance cannot
be used for extra pack bytes. Oversized HTTP bodies return 413 and are cleaned up. The product ceiling is
2,147,483,648 compressed pack bytes per push, inclusive; configuration cannot
raise it. Repository/worktree size is not this metric. No automatic multi-push
splitting or second client-side pack preparation is added. Desktop classifies
explicit byte-limit/413 rejection before generic disconnect text; a confirmed
success receipt still wins. Other object, quota and work limits remain independent.

`GIT_MAX_STORED_PACK_BYTES` separately bounds completed packs and cold reads
(default 4 GiB). Thin-pack completion may exceed the incoming allowance while
remaining within this storage profile. Lowering upload policy does not change
cold-read capacity. Do not lower the storage profile below already published
packs on any serving reader. `GIT_PACK_SCRATCH_BYTES` defaults to 9 GiB per pack
workspace. Startup rejects nonpositive/inconsistent profiles: stored capacity
must cover receive capacity; scratch must cover two stored packs plus 64 MiB
of index/metadata headroom. Additional thin-base work still obeys that scratch cap.

These are configured ceilings, not proven maximum-size service capacity. Scratch
excludes HTTP input spools, cold-download staging, other workers and retained cache
entries. Deployment disk sizing/admission must account for all of them; this
change does not create a global disk reservation scheduler. The limits remain
64 MiB per object, 100,000 objects and 300 seconds of owned work. Helpers use
bounded mmap/delta caches and finite CPU, file, FD and Linux address-space limits.
macOS does not enforce Linux RLIMIT_AS. LFS and a larger single-object profile
are not implemented by changing pack admission.
The cache admits at most four immutable packs per API process, with leased entries
protected from eviction. Multipart holds at most four 8 MiB parts, subject also
to existing operation/process I/O credits. Parts cross object boundaries.
These are separate resource bounds, not a 256 MiB repository-size ceiling.

`GIT_MAX_EXPANDED_BYTES` retains its deployment name but means cumulative logical
traversal/verification work. Its default is 8 GiB, shared by native reads,
publication factory and projection reads. Explicit lower deployment values still
apply. Per-object memory and scratch limits remain independent. Metadata and
projection-output bounds remain finite; this is not unlimited repository support.

## Release and rollback

Apply `20261022030000_expand_standard_git_pack_locations.sql` first. It preserves
existing rows, ACLs and pin/generation/epoch checks and only expands accepted key
suffixes. Deployment must prevent new pack writes until **all** serving readers
support `.pack`: use a reader-first release or drain Git writes and replace the
serving set before reopening writes. This candidate does not orchestrate that
hosted rollout. Nixpacks now explicitly installs stock Git.

After any standard pack has been published, application rollback must retain the
new reader; rolling back to a POB-only reader is unsafe. Do not revert the SQL
suffix expansion or erase failed tasks/data as rollback. This is a compatibility
expansion, not a table migration or a change of PG ref authority.
