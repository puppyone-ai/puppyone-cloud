## ADDED Requirements

### Requirement: Bounded transaction capacity admission
Capacity allocation SHALL authorize once per project/publication metadata page,
independent of the number of objects, within a short PostgreSQL transaction.
Current row locks, quotas, typed identities and time-based fences SHALL remain
effective. No authorization context SHALL survive or bypass a later transaction.

#### Scenario: Large initial or incremental pack
- **WHEN** one page contains 6,017 objects for one live admitted Runtime writer
- **THEN** credential and Project role resolution each occur once for capacity admission
- **AND** quota, per-object validation, allocation and I/O claims commit atomically

#### Scenario: Permission or lease no longer valid
- **WHEN** a writer is revoked before admission or its credential, lease, pin or entitlement expires while the transaction waits
- **THEN** the entire reservation fails without counters, events or claims surviving

#### Scenario: Compatibility and retry
- **WHEN** an older caller uses the 200-object RPC or a retry supplies existing objects
- **THEN** the same admission and quota rules apply without double charging
- **AND** each independent I/O identity retains its own settlement ownership

#### Scenario: Failed release
- **WHEN** SQL migration fails before commit
- **THEN** prior functions, triggers, grants and populated data remain intact
