# Delivery plan

## Third slice (user approved 2026-10-22)
- [x] Reuse bounded request-local physical readback evidence in typed closure verification.
- [x] Consolidate Desktop remote probes and bind immutable push attempts to durable results.
- [x] Replace capacity/location page loops with set-based checked SQL.
- [x] Validate corruption, stale context, rollback, quota races and result recovery.
- [x] Run repository gates and synchronize reviewed changes without touching unrelated work.

## 1. Proposal and measurement
- [x] 1.1 Inspect current authority, transport, batch sizes, resource limits and measured failure.
- [x] 1.2 Specify byte segmentation, resource budgets, metadata staging, publication, reads and GC.
- [x] 1.3 Review architecture decisions and approve local experimentation (user confirmation, 2026-10-22; hosted changes excluded).
- [ ] 1.4 Add per-phase and per-RPC timing; archive reproducible sanitized baseline artifacts.

## 2. Control path and bounded ingestion
- [ ] 2.1 Consolidate Desktop preparation and duplicate remote checks without stale authorization.
- [ ] 2.2 Implement owned operation identity and authoritative result receipts.
- [ ] 2.3 Design/migrate byte-credit staging capacity, cancellation-safe I/O ownership and batch RPC/COPY interfaces.
- [x] 2.4 Replace per-100-object publication rounds with bounded byte batches and backpressure using existing POB storage (first implementation slice; existing checked metadata pages retained).
- [ ] 2.5 Prove capacity, current authorization, late I/O, GC fencing and final CAS semantics remain enforced.

## 3. Segmented pack representation
- [ ] 3.1 Implement versioned manifest and dual-format placement readers with 64-bit logical offsets.
- [ ] 3.2 Implement incoming segment writes, digest validation and bounded Range reads.
- [ ] 3.3 Implement cross-segment/forward deltas, external base attachments and expansion/depth budgets.
- [ ] 3.4 Stage object/proof/index pages; seal metadata without a final transaction proportional to all object rows.
- [ ] 3.5 Bind durable readback receipts to object closure proofs without accepting unverified cache bytes.
- [ ] 3.6 Implement new-format GC/compaction and pin-safe placement changes before enabling writes.

## 4. Scale and recovery
- [ ] 4.1 Introduce finite admitted large-push profiles and replace incompatible hardcoded body/graph/disk/time limits.
- [ ] 4.2 Bound per-task and global RAM, incoming NVMe, CPU, network, DB connections and object-store concurrency.
- [ ] 4.3 Prove worker crash, response loss, stale owner, quota exhaustion, concurrent ref updates and retries.
- [ ] 4.4 Preserve standard Git compatibility; do not claim client resumability from storage retries.
- [ ] 4.5 Separate background maintenance capacity from interactive pushes and prove tenant fairness.

## 5. Acceptance and release
- [ ] 5.1 Pass meaningful unit and integration tests, including native authority and storage compatibility gates.
- [ ] 5.2 Pass complete local fixed-fixture initialization <5 seconds with empty destinations and read/fetch validation.
- [ ] 5.3 Measure large admitted packs, tiny updates on large histories, metadata-heavy and delta-adversarial inputs.
- [ ] 5.4 Verify real colocated PostgreSQL/S3 latency, request counts, byte amplification and concurrency separately.
- [ ] 5.5 Complete populated migration/rollback-reader checks, then follow the repository's separate deployment process.

## First-slice evidence (2026-10-22)
- [x] Centralize byte-window, entry-count, in-flight byte and concurrency policy.
- [x] Route native Git and mapping writes through the same bounded storage path.
- [x] Keep per-window capacity identities, physical readback and checked location registration.
- [x] Join started work on producer/I/O errors, repeated cancellation and bridge timeout.
- [x] Add component tests and a reproducible fixed-pack request-count comparison.
- [x] Record final post-change backend gate (3,323 passed; see verification.md).

At the first slice, only task 2.4 was delivered from the complete architecture. The per-phase stream
metrics are a subset of 1.4; local component tests are not live PG/S3 acceptance
for 2.5 or 5.2–5.5. No schema or persisted-format migration was part of that slice.

## Second slice: prepared ingestion metadata
- [x] Admit exact known object facts once per bounded byte page, under one operation I/O ID.
- [x] Separate metadata pages from S3 byte windows; collect only read-back placements.
- [x] Join owned work and settle once; keep uncertain or incomplete operations fenced.
- [x] Add atomic service-role page RPCs preserving existing checked primitives and old interfaces.
- [x] Page final capacity reconciliation/seal and dependency-ordered proof registration by bytes.
- [x] Test rollback, competing quota, namespace, stale owner, cancellation and cold reads.
- [x] Measure fixed-pack upload DB calls: 69 to 3; 5 PUT + 5 readbacks retained.
- [x] Pass final backend gate (3,333 passed) and disposable native PostgreSQL checks (73 passed).
- [ ] Complete hosted/PostgREST/S3 and populated Docker release admission before deployment.

This is a subset of 2.3/2.5, not completion of the future staging-credit, worker
recovery, proof-reuse or segmented-pack architecture. The <5-second end-to-end
target remains unaccepted; local component timings do not substitute for it.
