## ADDED Requirements

### Requirement: Byte-bounded logical pack storage
The system SHALL preserve each incoming logical Git pack in a versioned,
immutable segmented representation whose physical segment limit is expressed
in compressed bytes, independent of Git object count. Physical segments SHALL
NOT be treated as individually valid Git packs. Existing placements SHALL remain
readable during the rollout.

#### Scenario: Small and large packs
- **WHEN** admitted input exceeds the configured segment byte limit
- **THEN** the receiver seals additional bounded segments and a final tail
- **AND** all segments belong to the same logical pack and publication operation
- **AND** input below the limit produces one tail segment without padding

#### Scenario: Cross-segment object or delta
- **WHEN** a compressed object or delta base crosses a segment boundary
- **THEN** the virtual reader resolves its original 64-bit logical offsets
- **AND** reconstructed canonical object bytes match their Git OID

### Requirement: Bounded ingestion and global admission
The system SHALL bound per-operation and global memory, incoming scratch,
network concurrency, metadata buffers and decoding work. It SHALL apply
backpressure before exceeding admitted resources and retain finite byte,
object, dependency and time profiles. Existing repository materialization and
server-side Git subprocesses SHALL NOT be introduced.

#### Scenario: Slow storage during a large push
- **WHEN** storage is slower than the sender and the bounded queue fills
- **THEN** the receiver slows input rather than buffering the remaining pack
- **AND** other admitted tenants retain their scheduled resource shares

#### Scenario: Input exceeds its resource profile
- **WHEN** compressed bytes, decoded work or object limits exceed admission
- **THEN** the operation fails without publishing refs or exhausting the worker

### Requirement: Safe delta dependencies
The system SHALL resolve thin-pack external bases only from an admitted
reachable snapshot. Segmented pack placements SHALL include verified,
self-contained external base representations or reject the input within its
resource budget. It SHALL count base materialization cost and SHALL NOT create
unbounded cross-pack decoding dependency chains.

#### Scenario: Thin pack with an existing base
- **WHEN** incoming data requires an authorized base absent from its own pack
- **THEN** the base is resolved under the live snapshot and persisted as protected full base data
- **AND** its new physical representation is verified before publication
- **AND** an unreachable or missing base rejects the operation

### Requirement: Batched metadata and operation-level capacity
The system SHALL stage positions, typed object facts and proofs in bounded byte
pages under an operation identity. It SHALL reserve finite staging credits
before physical writes, enforce exact canonical capacity before publication,
and avoid repeating full admission per object batch. Metadata page count and
credit renewals SHALL be reported rather than described as constant work.

#### Scenario: Metadata exceeds one page
- **WHEN** a large push produces multiple pages of object metadata
- **THEN** the receiver stages bounded pages without a giant request or long-held ref lock
- **AND** pages do not independently publish refs or bypass current authority

#### Scenario: Capacity is exhausted
- **WHEN** a write would exceed its reserved staging or canonical capacity
- **THEN** the operation obtains authorized additional credit or stops before that write/publication

### Requirement: Durable verification and atomic publication
The system SHALL read back newly written physical bytes and bind verification
evidence to immutable placement, generation, pin and GC epoch. RefTransactionService
SHALL atomically publish accepted catalog visibility, refs, result receipt and
required audit/events only after closure, policy, quota and CAS checks. Final
publication SHALL use sealed staged facts without rewriting all object rows.

#### Scenario: New bytes have not passed durable readback
- **WHEN** uploads completed but byte verification or closure proof is incomplete
- **THEN** no new ref is visible and the operation cannot report accepted

#### Scenario: Prepared uploads already passed physical verification
- **WHEN** physical readback, checked location registration and settlement all succeed under the current publication pin
- **THEN** the closure walker may reuse bounded typed facts tied to those placements without another S3 read
- **AND** it still verifies dependency types, graph budgets and logical aggregates
- **AND** failed writes, expired contexts, other Projects and overflowing evidence use no unverified shortcut

#### Scenario: Concurrent quota or ref state changes
- **WHEN** capacity/catalog epochs or expected refs change after sealing
- **THEN** stale facts are resealed or the publication conflicts
- **AND** no stale capacity count or ref value is silently accepted

### Requirement: Fenced recovery and stable result identity
The system SHALL persist operation identity and final results and fence late
I/O by owner epoch. Cancellation SHALL retain protection until in-flight writes
are settled or safely recoverable. A lost response SHALL NOT be treated as a
negative publication result or cause automatic creation of a new operation.

#### Scenario: Successful commit with a lost HTTP response
- **WHEN** PostgreSQL accepted the operation but the client lost the response
- **THEN** querying the original identity returns the committed result
- **AND** changed target/input under the same identity is rejected

#### Scenario: Initial push result remains absent
- **WHEN** a known Desktop attempt has no decisive durable receipt or matching remote ref
- **THEN** reconciliation keeps the same uncertain attempt and does not start another push

#### Scenario: Normal Desktop initial push acknowledgement
- **WHEN** stock Git reports successful expected-absent main publication
- **THEN** Desktop can finish local bookkeeping without additional remote discovery
- **AND** local identity and credential ownership checks remain enforced

#### Scenario: Worker crash during upload
- **WHEN** a worker fails with partial segments or late writes
- **THEN** incomplete data remains unpublished and fenced
- **AND** recovery or GC respects I/O completion bounds and active pins
- **AND** storage retry does not imply stock Git client byte-level resumability

### Requirement: Incremental reads and physical dependency retention
Reads SHALL resolve authorized OIDs through bounded indexed range reads and
delta decoding. Incremental publication SHALL reuse retained typed proofs at
existing-history boundaries. GC and compaction SHALL preserve physical base
dependencies and all pinned placements while allowing background replacement.

#### Scenario: Small update to a large verified repository
- **WHEN** a push changes a small amount of data in a large verified history
- **THEN** verification works on new facts and required bases without listing or downloading the old repository

#### Scenario: Compaction races with an active reader
- **WHEN** background compaction replaces a logical container
- **THEN** its replacement is verified before placement activation
- **AND** old data and required delta bases survive until dependent pins release

### Requirement: End-to-end performance evidence
The fixed accepted 411-commit, 6,013-object, approximately 20 MB fixture SHALL
complete full local initialization in less than five seconds under documented
running-service/logged-in conditions with a fresh empty target. Larger admitted
inputs SHALL be evaluated for throughput, bounded resource use, incremental
work and request/byte amplification rather than a universal five-second bound.

#### Scenario: Fixed fixture acceptance
- **WHEN** the user confirms initialization for a fresh test destination
- **THEN** timing includes Project, Access, Remote, Upload and Finish through durable accepted refs and client success
- **AND** subsequent read/fetch validates the fixed source commit
- **AND** the report distinguishes local, real cloud, and component benchmarks

#### Scenario: Scale evaluation
- **WHEN** admitted test inputs increase in bytes or object count
- **THEN** reports include phase timings, RPC counts, payload bytes, memory, scratch, queueing, retries and delta read amplification
- **AND** no test claims support beyond the enabled resource profile
