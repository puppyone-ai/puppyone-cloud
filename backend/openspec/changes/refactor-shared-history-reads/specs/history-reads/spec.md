## ADDED Requirements
### Requirement: Admitted reusable History reads
The system SHALL reuse immutable pack reads and graph summaries only within current project authorization and an admitted repository snapshot. Publication proof SHALL retain fresh durable readback.

#### Scenario: Repeated summaries
- **WHEN** the same refs are queried twice
- **THEN** both requests authorize and capture current refs, but the second does not download Git objects

#### Scenario: Shared build cancellation
- **WHEN** one of two subscribers disconnects
- **THEN** the other continues; when all disconnect, work stops and releases its pin after I/O drains

### Requirement: Bounded metadata and lazy details
The system SHALL page object placements without a repository-wide object-count ceiling and SHALL return summaries without tree diffs when requested.

#### Scenario: Large repository
- **WHEN** more than 50,000 placement rows exist
- **THEN** metadata is read in bounded pages and stored in a disposable disk index

#### Scenario: Detail authority
- **WHEN** a client requests details for a commit absent from current reachable history
- **THEN** the request is rejected even if earlier cache entries contain that commit
