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

### Requirement: Provider-only runtime extensions
New suppliers implementing the existing capability contract SHALL be registered within the provider package without supplier-specific changes to outer Agent runtime logic. Connections SHALL carry resource identity and transition results SHALL distinguish retained files from retained processes.

#### Scenario: A third provider implements the existing contract
- **WHEN** the provider is registered and selected for a new workspace
- **THEN** the unchanged workspace file and task paths use its bound handle and lifecycle results

### Requirement: Reusable authorized file channels
File operations SHALL reuse bounded channels for the same workspace resource and compute epoch while retaining per-operation authorization and activity admission. Watches SHALL not extend idle compute activity. Ambiguous mutations MUST NOT be replayed.

#### Scenario: Repeated warm file operations
- **WHEN** an authorized user reads and saves multiple files in one running workspace
- **THEN** operations reuse the verified channel without a provider inspection or new remote controller per request

#### Scenario: Workspace transition or uncertain result
- **WHEN** compute epoch changes, a channel disconnects, or a request times out
- **THEN** the old channel is invalidated and an uncertain write is surfaced without automatic replay
