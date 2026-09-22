## ADDED Requirements

### Requirement: Desktop OAuth state is cluster safe

The service SHALL store desktop OAuth state and exchange codes in a shared TTL store and SHALL consume each value atomically at most once.

#### Scenario: Requests cross replicas

- **WHEN** start, callback and exchange reach different application instances
- **THEN** the flow succeeds once and replay is rejected

