## ADDED Requirements

### Requirement: Exact Hosted Workspace SSH access
The SSH adapter SHALL grant native shell access only to the issuing user's
current workspace and generation using proof of possession of a registered public key.

#### Scenario: External agent opens the editor's workspace
- **WHEN** a valid single-use grant authenticates with its matching private key
- **THEN** shell and editor access the same uncommitted files without starting a Loop.

#### Scenario: Unauthorized or stale connection
- **WHEN** a key, owner, generation, expiry or Project authorization does not match
- **THEN** access is denied before shell execution.

### Requirement: Durable provider-neutral connection lifecycle
Workspace SHALL own connection occupancy and cleanup independently of SSH or Provider implementations.

#### Scenario: Connection drops with descendants running
- **WHEN** SSH disconnects or its lease cannot renew
- **THEN** the exact task is stopped and occupancy remains until cleanup acknowledgement.

#### Scenario: Workspace is busy
- **WHEN** pause or retirement is requested with an active or unconfirmed connection
- **THEN** the resource is preserved until connection cleanup succeeds.
