## ADDED Requirements
### Requirement: Reuse admitted opening work
The system SHALL reuse a fresh root listing for preparation-to-display handoff only within the same session, API, target and capability scope. A handoff SHALL be bounded, single-use and invalidated on mutation or authority failure. Every actual file request SHALL retain authoritative admission and final revocation checks.

#### Scenario: Running workspace
- **WHEN** a user opens an already running workspace
- **THEN** the client verifies live file access without requesting a compute start, and display consumes the preparation result without reopening the workspace.

#### Scenario: Authority changes during preparation
- **WHEN** the account, session, API or target changes, or a workspace invalidation is observed
- **THEN** the prior handoff cannot disclose files or activate the new selection.

### Requirement: Owned transport reuse
The local supervisor SHALL own an isolated Fly agent across source reloads, with app and credential identity validation, configuration rotation and deterministic shutdown. Channel pools SHALL remain bounded and never replay uncertain writes or confer authority.

#### Scenario: Source reload
- **WHEN** API and worker restart with unchanged transport credentials
- **THEN** they may reuse only their supervisor-owned private agent, without using the user's default Fly configuration.

#### Scenario: Revocation or replacement
- **WHEN** authorization is revoked or the resource binding changes during a request
- **THEN** the result is rejected even when the transport was reused.
