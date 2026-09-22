## ADDED Requirements

### Requirement: Project-owned canonical repository

PuppyOne MUST maintain exactly one canonical repository history and object store
per Project. Project root MUST be the Project resource itself, not a synthetic
Scope resource.

#### Scenario: Project root locator
- **WHEN** a client resolves `/git/{project_id}.git`
- **THEN** the target is `{kind: project_root, project_id}`
- **AND** no Scope ID or root Scope row is required

#### Scenario: Scoped locator
- **WHEN** a client resolves `/git/{project_id}/scopes/{scope_id}.git`
- **THEN** the target is `{kind: scope, project_id, scope_id}`
- **AND** the Scope MUST be a non-empty path boundary owned by that Project

### Requirement: True Scope persistence

The database MUST persist only real non-root Scope resources and MUST enforce
canonical non-empty paths, unique Project paths, and exact Project ownership.

#### Scenario: Empty Scope path
- **WHEN** a caller attempts to create a Scope whose canonical path is empty
- **THEN** the operation is rejected
- **AND** no root sentinel row is created

#### Scenario: Cross-Project Scope reference
- **WHEN** a Surface or Binding references a Scope from another Project
- **THEN** the database rejects the write through a composite foreign key

### Requirement: Root-or-Scope associations

Access Surfaces and Workspace Bindings MUST use Project ID plus an optional Scope
ID as their only persisted target representation.

#### Scenario: Project root association
- **WHEN** an Access Surface or Binding targets the Project root
- **THEN** `project_id` is set and `scope_id` is NULL
- **AND** no `is_root` or `binding_kind` fact is persisted

#### Scenario: Scoped association
- **WHEN** an Access Surface or Binding targets a Scope
- **THEN** `scope_id` is non-null and belongs to the same Project

### Requirement: Project-first human authorization

Every human root or scoped control-plane request MUST authorize the current
ProjectGrant before resolving optional Scope geometry. Locators, bindings, and
machine credentials MUST NOT create human authorization.

#### Scenario: Authorized scoped context
- **WHEN** a signed-in user resolves a scoped canonical locator
- **THEN** the backend authorizes the Project action first
- **AND** then verifies the exact Scope belongs to that Project

#### Scenario: URL without Project grant
- **WHEN** a user knows a Project or Scope locator but lacks Project access
- **THEN** no Project or Scope metadata is disclosed

### Requirement: Exact machine target admission

Git machine admission MUST resolve credential, active Git Surface, target,
optional Binding, current human access, and effective mode in one database
snapshot before creating a typed RuntimeGrant.

#### Scenario: Root credential at scoped URL
- **WHEN** a root-target credential is presented to a scoped URL
- **THEN** Git authentication fails with the non-enumerating credential error

#### Scenario: Scoped credential at root URL
- **WHEN** a Scope-target credential is presented to a root URL
- **THEN** Git authentication fails with the same non-enumerating error

#### Scenario: Valid target
- **WHEN** route, credential, Surface, optional Binding, Project, and Scope match
- **THEN** admission creates one ResolvedRepositoryView and RuntimeGrant
- **AND** downstream Version Engine code does not infer identity from raw DTOs

### Requirement: One repository with bounded views

Project root and Scope targets MUST use the same canonical root, object store,
refs/CAS transaction authority, audit, conflicts, and GC. Scope views MUST only
narrow path, excludes, and mode.

#### Scenario: Scoped write
- **WHEN** a valid Scope RuntimeGrant writes within its boundary
- **THEN** the write publishes through the canonical Project root transaction
- **AND** no independent Scope repository or source of truth is created

#### Scenario: Non-root hosting readiness
- **WHEN** only a non-root Scope has an accepted push
- **THEN** Project-wide Claude or hosted runtime readiness remains false

### Requirement: Scope-independent Access Surfaces

Scope creation MUST define only a path boundary. Access Surface and credential
creation MUST be an explicit authorized operation with hash-only storage and
one-time secret reveal.

#### Scenario: Create Scope without Git
- **WHEN** a user creates a new Scope
- **THEN** the Scope can exist without a Git or CLI Surface

#### Scenario: Enable Git for target
- **WHEN** an authorized user explicitly enables Git for Project root or Scope
- **THEN** the exact Surface is created or reused atomically
- **AND** any new raw credential is revealed once and stored only as a hash

### Requirement: Canonical client target contract

Backend, Web, and Desktop MUST exchange a discriminated target object and MUST
not expose a synthetic root Scope ID, root Scope object, `is_root`, or
`full/scoped` binding kind.

#### Scenario: Root response
- **WHEN** the backend returns a Project-root Binding or canonical context
- **THEN** `target.kind` is `project_root`
- **AND** no Scope object or Scope ID is present

#### Scenario: Scope response
- **WHEN** the backend returns a scoped Binding or canonical context
- **THEN** `target.kind` is `scope`
- **AND** the exact Scope ID and current path facts are present

### Requirement: Single final cutover

The release MUST use a preflighted, backed-up, exact-SHA-gated migration and
MUST end with only the final schema and contract. Runtime dual read/write and
old response fallback are forbidden.

#### Scenario: Corrupt legacy facts
- **WHEN** preflight finds malformed roots, mismatched Bindings, orphan
  credentials, cross-Project references, or duplicate builtin Surfaces
- **THEN** the migration aborts before destructive writes

#### Scenario: Old client
- **WHEN** an unsupported Desktop protocol version calls the new contract
- **THEN** it receives an explicit upgrade-required error
- **AND** the backend does not serialize the old target shape
