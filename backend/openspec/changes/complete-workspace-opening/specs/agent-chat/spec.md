## ADDED Requirements
### Requirement: Consuming connection establishes workspace readiness
The system SHALL distinguish provider-started compute from usable files. Existing workspace resume SHALL not require a separate lifecycle SSH check. The connection which proves native file availability SHALL remain available to the consuming process and return its root listing without rereading it.

#### Scenario: Resume existing files
- **WHEN** a paused workspace resumes
- **THEN** its lifecycle owner starts the persisted resource and records a fenced handoff
- **AND** a human-authorized consumer verifies runtime, generation and compute epoch through the actual file channel before publishing ready
- **AND** it returns the root read from that channel and retains the channel for later operations

#### Scenario: Revoked or superseded opening
- **WHEN** permission, generation, resource, compute epoch or operation ownership changes during an opening
- **THEN** the old operation cannot publish readiness or return file data
- **AND** neither retry nor a read creates replacement compute or discards persisted files

### Requirement: Bounded authenticated transport reuse
The Fly provider SHALL own bounded persistent SSH connections scoped to provider credentials and exact resource identity. It SHALL keep private credentials server-side, invalidate stale connections and never replay an uncertain mutation.

#### Scenario: File stream reconnect
- **WHEN** a file stream closes but its SSH transport remains healthy
- **THEN** a new channel reuses that authenticated transport and performs a fresh file-service binding handshake

### Requirement: Measured workspace opening acceptance
Opening acceptance SHALL report complete warm, reconnect and resume samples independently, including p50/p95 and connection/database call counts. Unit test success SHALL NOT count as latency acceptance.

#### Scenario: Targets not achieved
- **WHEN** real measurements exceed the 3/8/15 second median targets
- **THEN** the delivery records the actual measurements and remaining limiting stages without marking latency acceptance complete
