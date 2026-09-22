## ADDED Requirements

### Requirement: Canonical Git locator discovery

Desktop MUST treat a trusted canonical PuppyOne Git locator as deterministic
Project/Scope discovery while preserving Workspace Binding as durable local
identity and ProjectGrant as human authorization.

#### Scenario: Existing bound root workspace

- **WHEN** a local workspace has a canonical root locator and an active matching
  Workspace Binding
- **AND** the signed-in account retains Project read capability
- **THEN** Desktop opens the bound Project directly
- **AND** Desktop does not first enumerate or scan Organization Projects,
  Scopes, or credentials
- **AND** session restoration does not start a broad Project-catalog request in
  parallel with exact binding resolution

#### Scenario: Existing bound scoped workspace

- **WHEN** a local workspace has a canonical scoped locator and an active
  matching scoped Workspace Binding
- **THEN** Desktop opens the owning Project with the scoped-workspace state
- **AND** it does not represent the checkout as a complete Project or as
  satisfying root Git readiness

#### Scenario: Canonical locator without local binding

- **WHEN** local binding state is absent but a trusted canonical locator exists
- **THEN** Desktop may parse one Project/Scope candidate from the locator
- **AND** it verifies the current human ProjectGrant before displaying Project
  metadata
- **AND** it does not treat the locator or Git credential as an already-created
  Workspace Binding
- **AND** it does not perform an N-by-M Project/Scope/key scan

#### Scenario: Attach a selected Cloud Project

- **WHEN** the user chooses `Use here` for a Project whose ID is already known
- **THEN** Desktop creates the Workspace Binding using that exact Project ID
- **AND** the binding response supplies the canonical remote and credential
- **AND** Desktop does not call Repo Identity or list Scopes merely to derive
  the Project or remote URL before binding

### Requirement: Secret-free binding and remote persistence

Workspace Binding and shared workspace configuration MUST persist stable
identity only and MUST NOT persist a replayable Git runtime credential.

#### Scenario: Binding issuance

- **WHEN** a full or scoped Workspace Binding is created
- **THEN** the server returns canonical remote metadata and the one-time
  credential as separate fields
- **AND** Desktop stores origin, Project ID, Scope identity/kind, binding ID, and
  workspace instance ID in binding configuration
- **AND** Desktop stores the credential only through the Git credential helper

#### Scenario: Local configuration failure

- **WHEN** server-side binding issuance succeeds but local remote or credential
  configuration fails
- **THEN** the client retains enough binding identity to revoke or retry safely
- **AND** it does not leave an unreported active binding or silently rebind the
  workspace to another Project
