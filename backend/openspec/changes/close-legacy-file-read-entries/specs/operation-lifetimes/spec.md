## ADDED Requirements

### Requirement: Retired internal files have no runtime surface
All `/internal/nodes/*` routes SHALL be removed and archived outside executable packages.

#### Scenario: Old caller presents a valid internal credential
- **WHEN** any retired node route is requested
- **THEN** no handler or dependency executes and HTTP returns not found

### Requirement: Index source reads share one owned lifetime
An indexing operation SHALL bind its ProjectGrant once and read all input from one native snapshot, with bounded per-query execution and sequential file processing.

#### Scenario: HEAD changes while indexing
- **WHEN** another writer changes HEAD after the first file is read
- **THEN** remaining files come from the original snapshot

#### Scenario: Failure or cancellation
- **WHEN** source, embedding or vector I/O fails, or the task is cancelled
- **THEN** indexing does not report empty/partial success, in-flight reads are joined and the read pin is released

#### Scenario: Long task authorization expires
- **WHEN** 30 seconds have passed since the task's last successful authorization
- **THEN** its next source read or index write reloads current authorization facts without reusing an inherited HTTP cache
- **AND** denial or unavailability stops work without opening another snapshot

### Requirement: Agent metadata consumes the admitted grant
Agent metadata SHALL reuse the named Agent-read grant, preserve private-Agent filtering and read unique paths in one session.

#### Scenario: Denial or storage failure
- **WHEN** authorization fails
- **THEN** no Agent data or file content is read
- **WHEN** storage fails
- **THEN** the failure is not converted to missing metadata
