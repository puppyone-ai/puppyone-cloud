## ADDED Requirements

### Requirement: Product-managed Office engine

The system SHALL embed Office editing in PuppyOne while keeping Document Server
deployment URLs, JWT secrets, and callback handling exclusively in the PuppyOne
backend. Desktop users MUST NOT install Docker or configure an Office engine.

#### Scenario: User opens an editable Office file

- **WHEN** an authenticated Desktop user enables the Office experiment and opens a supported file
- **THEN** Desktop SHALL create a PuppyOne-managed session and embed its editor surface
- **AND** no Document Server URL, JWT secret, or local callback listener SHALL be supplied by the user

#### Scenario: Managed engine is unavailable

- **WHEN** the PuppyOne Office service is disabled, unhealthy, or unreachable
- **THEN** Desktop SHALL fail closed to the existing read-only local preview
- **AND** the local file SHALL remain available and unchanged

### Requirement: Ephemeral user-scoped sessions

The system SHALL bind each Office session to the authenticated user, store its
binary objects privately, and expire both metadata and access capabilities.

#### Scenario: Cross-user access attempt

- **WHEN** another authenticated user requests session status, result, save, or close
- **THEN** the system SHALL return not found without revealing session existence

#### Scenario: Session expiry

- **WHEN** the configured session TTL elapses
- **THEN** session capabilities SHALL stop authorizing source or callback access
- **AND** temporary objects SHALL be eligible for deletion

### Requirement: Bounded engine capabilities

The system SHALL expose only purpose-bound, expiring source and callback
capabilities to the document engine and SHALL validate the callback JWT,
document key, result format, result origin, redirect chain, and byte limit.

#### Scenario: Callback token does not match body

- **WHEN** a callback JWT or document key differs from the callback body or stored session
- **THEN** the callback SHALL be rejected without updating session state or result bytes

#### Scenario: Result URL leaves the allowlist

- **WHEN** a save result or any redirect targets an origin outside the configured allowlist
- **THEN** the result SHALL be rejected and SHALL NOT be fetched

### Requirement: Local atomic persistence

The system SHALL return monotonically versioned edited binaries to Desktop, and
Desktop SHALL write them only through its version-checked atomic binary writer.

#### Scenario: Local file remains unchanged

- **WHEN** Desktop receives a newer edited revision and the local base version still matches
- **THEN** Desktop SHALL atomically replace the file and acknowledge the new local version

#### Scenario: Local file changed externally

- **WHEN** the local base version no longer matches
- **THEN** Desktop SHALL preserve the edited bytes in recovery storage
- **AND** require an explicit keep-edited or keep-external resolution

