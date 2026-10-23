## ADDED Requirements

### Requirement: Durable registered-client identity
The system SHALL persist registered-client identity independently of protocol
credentials and retain the same identity through renewal and reconnect.

#### Scenario: Credential rotation
- **WHEN** an active client rotates its renewal credential
- **THEN** its registration and approved resources remain unchanged
- **AND** replay outside the original idempotent request cannot mint credentials.

### Requirement: Explicit delegated resource authorization
The system SHALL require current authorized approval of the exact resource,
capabilities, channels and duration before admitting a registered client.

#### Scenario: Invitation is forwarded
- **WHEN** another client holds only an invitation or approval identifier
- **THEN** it cannot redeem the applicant's authorization.

#### Scenario: A repository client attempts a Shell
- **WHEN** a client presents repository-only or read-only authorization to SSH
- **THEN** admission is denied before allocating execution resources.

### Requirement: Shared revocation lifecycle
The system SHALL associate protocol credentials and sessions with their parent
authorization and reject revoked or expired authority at admission and renewal.

#### Scenario: Owner revokes a durable grant
- **WHEN** revocation commits
- **THEN** new credentials and operations using that grant are denied
- **AND** active execution is closed by Workspace with durable cleanup tracking.

### Requirement: Compatibility without privilege expansion
Existing credentials SHALL retain their exact legacy permissions and unsupported
native Scope operations SHALL remain explicitly unavailable.

#### Scenario: Populated upgrade
- **WHEN** the additive lifecycle migration is applied to existing credentials
- **THEN** they do not acquire durable delegation or new resources
- **AND** existing SSH and repository admission continue to enforce their original limits.
