# Change: Scalable Git pack ingestion over PostgreSQL and S3

## Why

The measured initial push contained 6,013 reachable objects in an approximately
20 MB Git pack. The current adapter expands and re-encodes that pack, then
synchronously publishes batches capped at 100 objects. The failed attempt
completed 36 of approximately 61 planned batches in 301 seconds; its main
request made 217 database calls and 72 object-storage calls. Preparing the push
already took approximately 34 seconds. Changing a batch constant or replacing
S3 alone cannot meet the accepted full-local initialization target of <5 seconds.

The architecture must also handle larger admitted pushes with bounded memory,
incremental work, finite quotas, safe retries, and independent read/GC behavior.
A 20 MB benchmark is not a universal repository or request size limit.

## What Changes

- Preserve a logical incoming pack and store its compressed byte stream in
  immutable, byte-bounded S3 segments. Start evaluation at 64 MiB per segment;
  this is a tunable proposed default, not a GitLab parameter.
- Introduce bounded receiving, validation, upload/readback, and metadata-staging
  pipelines with backpressure and admission across all concurrent operations.
- Add a versioned segmented-pack placement/manifest and virtual offset reader,
  including internal delta dependencies and self-contained external base data.
- Replace per-100-object remote publication rounds with operation-level
  admission, byte-budgeted metadata pages, byte-credit reservations and a short
  final checked publication transaction.
- Preserve PostgreSQL refs, S3 object durability, the shared RefTransactionService,
  current authorization, typed closure proofs, quota/billing rules and GC fencing.
- Use immutable durable receipts to distinguish accepted, rejected and still
  uncertain operations without repeatedly reconstructing remote state.
- Make resource limits and request/byte amplification measurable. Existing hard
  limits remain in force until replacement large-push profiles are accepted.
- **BREAKING for storage readers:** enabling new-format writes requires readers
  and GC to support segmented pack placements first. Git wire compatibility
  remains unchanged; existing loose/POB objects remain readable.

## Impact

- New capability: `git-pack-ingestion` (orthogonal to the generic public S3 API).
- Related existing work: `remove-native-git-materialization`,
  `add-native-repository-authority`, `add-session-workspaces-and-object-proofs`.
  Canonical authority and reachability contracts in architecture documents 01,
  05 and 17 take precedence over older generic S3 requirements.
- Backend: Git router/execution/pack codec, storage placement reader/writer,
  publication verification, capacity/lease/proof adapters, checked SQL and GC.
- Desktop: consolidated preparation, immutable attempt identity, progress,
  result reconciliation; stock Git transport remains supported.
- New PostgreSQL migrations are required; direct COPY is an internal trusted
  transport into attempt-scoped staging, not an authorization bypass.
- No production mutation, code implementation, schema migration or performance
  success is implied by this proposal.

## Status

Architecture approved for local experimentation, 2026-10-22. The user then
requested implementation, starting with consolidation of chunking. The first
runtime slice implements byte-window ingestion with the existing POB format;
see `verification.md`. Segmented Git pack storage, database staging, Desktop
preparation and full <5-second acceptance remain outstanding. No hosted
mutation or deployment is implied. See `design.md` and `tasks.md` for delivery.

The user subsequently approved systematic metadata-path optimization. The second
slice uses exact facts from the already parsed bounded pack, one I/O identity,
bounded byte-page RPCs and one settlement. It retains final reconciliation and
all existing checked SQL primitives. See design section 10 for the explicit
boundary between this delivered POB optimization and future staging-credit /
segmented-pack work.
