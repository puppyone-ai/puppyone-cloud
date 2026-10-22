## ADDED Requirements

### Requirement: Standard pack persistence without incoming re-encoding
The native receiver SHALL use a bounded stock Git indexer and SHALL persist
self-contained standard packs without converting incoming objects into loose
objects or proprietary storage bundles. Only authorized external thin bases MAY
require an additional representation. No complete prior repository SHALL be
materialized for this operation.

#### Scenario: Full pack arrives
- **WHEN** a valid non-thin pack is received
- **THEN** its durable pack bytes are identical to the received bytes
- **AND** no cumulative raw-body spool or per-new-object encoding occurs

#### Scenario: Thin pack arrives
- **WHEN** a delta requires an external base
- **THEN** the base is authorized through the pinned published graph
- **AND** stock Git completes a self-contained durable pack
- **AND** an unauthorized base fails before publication

### Requirement: Pack writes preserve atomic publication and compatibility
Standard pack writes SHALL retain existing capacity admission, physical readback,
location registration, settlement, closure verification and PostgreSQL ref CAS.
Existing loose, POB and chunked locations SHALL remain readable. Fresh durability
proofs SHALL NOT rely on an earlier warm byte cache.

#### Scenario: Storage or metadata acknowledgement fails
- **WHEN** upload, checksum verification, registration or settlement fails
- **THEN** no publication-local verification evidence is promoted
- **AND** no ref is published from incomplete preparation

#### Scenario: Writer scratch is gone
- **WHEN** a standard pack has been persisted and incoming scratch is removed
- **THEN** a cold reader reconstructs exact original object bytes from durable storage
- **AND** corrupted durable data fails a fresh proof even if an older cache exists

### Requirement: Pack work has independent resource bounds
Pack handling SHALL bound process memory, scratch, object size/count, decoding
work, transfer concurrency and elapsed time independently. Logical aggregate
limits SHALL NOT be described as allocated memory or a required raw spool.
Cancellation SHALL join physical writes and reap child processes before releasing
owned resources. Multiple non-atomic ref updates SHALL share the incoming upload.

#### Scenario: Highly compressed repository
- **WHEN** logical object bytes exceed the former aggregate raw-spool cap but fit current admission
- **THEN** only compressed packs and indexes occupy the incoming disk cache
- **AND** publication and subsequent reads use aligned logical work limits

#### Scenario: Repeated cancellation during multipart
- **WHEN** the caller is cancelled repeatedly with physical parts in flight
- **THEN** all started parts are joined before abort and owner release
- **AND** no success or settlement is fabricated

#### Scenario: Receive allowance differs from storage resources
- **WHEN** a valid pack fits the effective deployment/plan receive allowance
- **THEN** no separate hardcoded 128 MiB receive ceiling is applied
- **AND** protocol metadata has a separate finite allowance that cannot bypass the pack limit
- **AND** thin completion uses an independently configured stored-pack budget

#### Scenario: Fixed maximum for a single push
- **WHEN** the compressed incoming pack exceeds 2,147,483,648 bytes
- **THEN** it is rejected before publication, without splitting it into additional pushes
- **AND** configuring a receive limit above 2 GiB fails at startup
- **AND** a pack exactly at the effective byte limit passes size admission, subject to other validation
- **AND** this limit does not measure the local worktree or cumulative repository size
- **AND** the same receiver enforces it for Desktop initialization, ordinary initial push and incremental push
- **AND** rejection leaves published refs, prior durable objects and local files unchanged

#### Scenario: Upload policy is reduced after publication
- **WHEN** new push admission is lowered below the size of an existing stored pack
- **THEN** new oversized pushes are rejected explicitly
- **AND** filesystem and S3 cold reads still use the unchanged storage resource profile

#### Scenario: Invalid resource profile
- **WHEN** receive, stored-pack or scratch budgets are nonpositive or inconsistent
- **THEN** application startup fails before serving requests

### Requirement: Compatibility expansion precedes new-format activation
The location schema SHALL accept both POB and standard pack keys while preserving
existing rows, privileges and fencing. Operators SHALL install compatible readers
before new-format writes. A rollback after publication SHALL retain pack readers.

#### Scenario: Populated upgrade fails or succeeds
- **WHEN** the additive migration fails transactionally or is retried successfully
- **THEN** pre-existing rows and ACLs are unchanged
- **AND** a successful upgrade admits both formats while rejecting stale pins and foreign keys
