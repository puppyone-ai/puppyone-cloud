## ADDED Requirements

### Requirement: Provider-independent live workspaces
The system SHALL compose Loop, Execution and Filesystem independently from the sandbox provider. Existing resources MUST retain their persisted provider, artifact and generation when deployment defaults change.

#### Scenario: Switch the default provider
- **WHEN** an operator selects Fly after creating an E2B workspace
- **THEN** new workspaces use Fly and the existing workspace continues to attach, pause, read files and clean up through its bound E2B adapter

#### Scenario: Unsupported recovery semantics
- **WHEN** a requested provider cannot implement the admitted recovery contract
- **THEN** admission fails explicitly before allocating or replacing any resource

### Requirement: Live native file visibility
The cloud file surface SHALL read and edit the same native working directory used by interactive Agent tasks, independently of Git publication. Interactive sessions for the same user and Project SHALL share that workspace; other users and automation executions SHALL remain isolated.

#### Scenario: A running Bash tool saves a file
- **WHEN** Bash saves or atomically replaces a file before the Run completes
- **THEN** an authorized browser receives an invalidation and can read the saved content without a Git commit

#### Scenario: An editor has an unsaved draft
- **WHEN** an Agent changes the same file
- **THEN** the browser retains the draft, exposes the changed base and does not silently overwrite either side

#### Scenario: A stale generation sends a file request
- **WHEN** a request names a replaced workspace generation or another owner
- **THEN** the backend rejects it before invoking the provider
