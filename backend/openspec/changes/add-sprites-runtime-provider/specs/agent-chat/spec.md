## ADDED Requirements

### Requirement: Sprites workspace provider
The system SHALL support explicitly selected Sprites workspaces through the
existing RuntimeProvider boundary, retaining native working files and existing
authorization, lifecycle, Agent and file-service protocols.

#### Scenario: Allocate and reconnect
- **WHEN** Sprites is configured with credentials and an immutable worker bundle
- **THEN** a deterministic, ownership-checked resource hosts the existing worker
- **AND** reconnecting cannot substitute another resource or worker artifact.

#### Scenario: Pause and resume
- **WHEN** the workspace lifecycle requests pause after releasing occupants
- **THEN** the provider releases its bounded hold and transport sessions
- **AND** acknowledges retained files only after Sprites reports warm or cold
- **AND** resume starts the file service without overwriting working files.

#### Scenario: Provider selection changes
- **WHEN** the deployment uses the Sprites default for new workspaces
- **THEN** existing bindings remain pinned to their original provider
- **AND** configured retained providers continue to receive API admission,
  Agent tasks, pending-open dispatch and lifecycle maintenance
- **AND** no migration, deletion or recreation is inferred from the new default.

#### Scenario: Default provider is unavailable
- **WHEN** creating a new workspace fails on Sprites
- **THEN** the operation reports failure without allocating through another provider
- **AND** existing Fly maintenance is not skipped because Sprites is unavailable.
