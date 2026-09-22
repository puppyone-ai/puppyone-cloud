## ADDED Requirements

### Requirement: Monotonic entitlement projection

PuppyOne MUST persist complete entitlement metadata and atomically prevent snapshot regression.
The same revision and payload hash MUST be idempotent; the same revision with different content
MUST be rejected and surfaced for reconciliation.

#### Scenario: Newer snapshot is published

- **WHEN** a valid snapshot has a source revision greater than the stored revision
- **THEN** PuppyOne atomically stores the snapshot and its audit event
- **AND** acknowledges the accepted revision and payload hash

#### Scenario: Older snapshot arrives

- **WHEN** a snapshot source revision is lower than the stored revision
- **THEN** PuppyOne rejects the update without changing product access

#### Scenario: Exact publication is retried

- **WHEN** the stored revision and payload hash equal the incoming publication
- **THEN** PuppyOne returns an idempotent acknowledgement

### Requirement: Published hosted entitlements are exact

Hosted DB mode MUST execute the validated published entitlement values without merging a second
plan matrix. Unknown features default closed; unknown mandatory limits or unsupported contract
major versions fail validation.

#### Scenario: Business snapshot is loaded

- **WHEN** PuppyOne loads a valid Business snapshot
- **THEN** it executes the published seat, storage, runtime, and feature values
- **AND** it does not fall back to Free or legacy Pro defaults

### Requirement: Billable seat saga

Any product change that increases billable human members MUST obtain and confirm sufficient paid
seat entitlement before granting active access. Pending billing members MUST NOT authorize product
or runtime access.

#### Scenario: Fifteenth billable member is proposed

- **WHEN** a Plus organization with fourteen billable seats proposes the fifteenth
- **THEN** PuppyPay returns a Business quote
- **AND** no member is activated until a billing manager explicitly accepts it and the new
  entitlement confirms sufficient seats

#### Scenario: Member is removed

- **WHEN** an active billable member is removed or suspended
- **THEN** product access is revoked immediately
- **AND** the seat decrease is submitted idempotently for billing reconciliation
