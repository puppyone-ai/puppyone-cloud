## ADDED Requirements
### Requirement: Replaceable Agent runtime capabilities
The Agent runtime SHALL retain its Access adapter ownership and compose Loop,
Execution and Files through separate interfaces. Provider lifecycle SHALL remain
owned by the workspace lifecycle, independently of the selected capability adapters.

#### Scenario: Replace a capability
- **WHEN** composition injects a different Loop, execution boundary or filesystem adapter
- **THEN** orchestration uses that interface without interpreting provider or Harness internals
- **AND** the adapter does not implicitly allocate or destroy another capability's resource.

#### Scenario: Preserve the current deployment
- **WHEN** the existing sandbox adapters are selected
- **THEN** task admission, approvals, conversation checkpoints, workspace recovery and Git publication retain their current contracts
- **AND** shared workbench admission remains disabled unless its lifecycle owner is explicitly installed.

#### Scenario: Compose Harness tools
- **WHEN** the host supplies filesystem and execution tools
- **THEN** the Harness uses those supplied definitions
- **AND** a read-only configuration does not construct mutation or process-execution tools.
