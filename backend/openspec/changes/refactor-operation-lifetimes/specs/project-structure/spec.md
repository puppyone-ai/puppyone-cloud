## ADDED Requirements

### Requirement: Explicit operation and resource lifetimes
The backend SHALL share process storage resources while keeping grants and pinned read sessions operation-scoped.

#### Scenario: Concurrent request services
- **WHEN** two requests construct search, tool or table services
- **THEN** they reuse the process engine and do not share mutable authorization state

#### Scenario: Composed hosted read
- **WHEN** an operation checks a path, lists or reads files and returns a revision
- **THEN** those reads use one admitted snapshot and release its pin on success or failure

#### Scenario: Cancellation
- **WHEN** an async caller cancels an owned blocking operation
- **THEN** cancellation stops new work and joins existing work before releasing its resources

#### Scenario: Interactive admission pressure
- **WHEN** running and waiting read budgets are full
- **THEN** the next operation fails with explicit retryable overload rather than accumulating unbounded work
