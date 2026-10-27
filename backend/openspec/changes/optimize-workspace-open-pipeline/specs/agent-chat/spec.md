## ADDED Requirements
### Requirement: Atomic opening admission
The worker SHALL claim the exact persisted opening intent, publish its initial phase and obtain current policy facts in one database transaction. Canonical authorization SHALL run before provider operations. No transaction SHALL span provider network I/O.

#### Scenario: Concurrent and stale workers
- **WHEN** workers race or an old pending row names a replaced generation/deadline
- **THEN** only the current claim can proceed and a stale contender cannot replace it
- **AND** ownership, resource and files remain preserved across retry

### Requirement: Overlapped consuming transport preparation
An authorized observer of an explicit opening MAY prepare the consuming private route and credentials while compute resumes. It SHALL NOT start compute, open a file channel or read content during transport preparation.

#### Scenario: Compute and route become ready independently
- **WHEN** a valid existing resource is resuming
- **THEN** route preparation overlaps worker startup
- **AND** file access waits for the matching operation's durable handoff and current authorization

#### Scenario: Interrupted or replaced observer
- **WHEN** the last observer disconnects, the opening expires, or its identity changes
- **THEN** observer tasks and reservations are released without cancelling the durable open intent or publishing stale readiness

### Requirement: Bounded status observation
Status waiting SHALL have bounded duration and concurrency, coalesce observers only within the same user/project, pace database observations and return current authority. It SHALL preserve ordinary status clients and never start compute without an explicit opening intent.

#### Scenario: Slow response needs no extra client delay
- **WHEN** Desktop receives a pending response after a bounded server wait
- **THEN** it may observe again without an additional fixed one-second sleep
- **AND** older servers returning immediately still receive rate-limited polling
