## ADDED Requirements
### Requirement: Live workspace file mutations keep audit order off the response path
The system SHALL use a workspace file channel only after the SQL revision fence and the runtime billing check pass. A mutation's intent audit SHALL exist before the mutation is sent, and SHALL be followed by a `rejected` or `completed` row written outside the reply path; a missing follow-up SHALL mean an unknown outcome.

#### Scenario: Warm save
- **WHEN** a warm, policy-authorized session saves a file
- **THEN** the revision fence, billing check and intent audit run concurrently and all complete before the sandbox write
- **AND** the reply does not wait for the completion audit

#### Scenario: Fence rejects after an early intent
- **WHEN** the revision fence rejects a request whose intent was already written
- **THEN** no file channel is used and the intent is closed with a `rejected` row

#### Scenario: Unauthorized caller
- **WHEN** a caller without a cached authorized snapshot requests a mutation
- **THEN** admission completes before any audit row is written

### Requirement: Final authorization guards returned file bytes
The system SHALL re-authorize before returning file bytes from a read. A mutation reply, which carries no file bytes, SHALL be returned without a second authorization once the mutation was admitted under the current fence.

#### Scenario: Read after revocation
- **WHEN** authority is revoked while a read is in flight
- **THEN** the read bytes are not returned

#### Scenario: Write receipt after revocation
- **WHEN** authority is revoked while an admitted write is in flight
- **THEN** the write's receipt is returned and the next request is refused
