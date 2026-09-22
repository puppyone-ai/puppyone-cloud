## ADDED Requirements

### Requirement: Explicit workspace binding

The system MUST identify a local workspace and Cloud Project through an explicit,
revocable Project-level binding that grants no authorization by itself.

#### Scenario: Create a full binding

- **WHEN** an authorized human binds a stable workspace instance to the
  canonical root scope
- **THEN** one active full binding is created
- **AND** a separate hash-only credential is issued with mode no greater than
  the human capability or root scope mode

#### Scenario: Non-root remote

- **WHEN** a remote identifies a non-root scope
- **THEN** only a scoped candidate/binding can be produced
- **AND** it does not represent a complete Project or satisfy Claude readiness

#### Scenario: Access revoked

- **WHEN** membership, role, account, host, binding, or binding credential changes
- **THEN** the next Cloud request re-evaluates authorization and fails closed
- **AND** local files remain available
