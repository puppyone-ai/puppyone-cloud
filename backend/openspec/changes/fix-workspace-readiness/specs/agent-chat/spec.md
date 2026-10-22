## ADDED Requirements
### Requirement: Durable workspace readiness
A user-authorized open SHALL persist an idempotent request. Background owners SHALL use database fencing, preserve uncertain resources, report bounded failure, and declare running only after the runtime protocol, file binding and root read succeed. HTTP cancellation SHALL NOT own resource lifetime.

#### Scenario: Request disconnect and owner restart
- **WHEN** the client disconnects or the owner stops during initialization
- **THEN** a subsequent owner resolves the same persisted resource intent after lease expiry without allocating a replacement workspace

#### Scenario: Incompatible artifact or credential
- **WHEN** the provider credential is rejected or the artifact lacks required capabilities
- **THEN** the system reports a typed failure and never presents a ready workspace

### Requirement: Provider-independent lifecycle presentation
Desktop SHALL consume workspace state and operation outcomes without choosing providers. Pausing, starting, failure and transport interruption SHALL remain distinguishable.

#### Scenario: Opening while pausing
- **WHEN** an authorized user opens a workspace with an in-progress pause
- **THEN** its durable open intent survives the pause and resumes the same workspace, or reports a bounded explicit failure
