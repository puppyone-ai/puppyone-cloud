## ADDED Requirements

### Requirement: Contextual Desktop Project resolution

Desktop MUST resolve an open Local workspace to one authorized Project/Scope,
local-only, or a recovery state without enumerating Organization Projects.
Canonical locator identity, human ProjectGrant, durable WorkspaceBinding, and
machine RuntimeGrant MUST remain independent.

#### Scenario: Authorized canonical locator without binding

- **WHEN** an open Local workspace has one trusted canonical Project or Scope
  locator and no usable WorkspaceBinding
- **AND** the current JWT has Project read capability
- **THEN** the backend returns the exact authorized Project/Scope context
- **AND** Desktop enters that Project without confirmation or catalog loading
- **AND** no binding, credential, Git configuration or content is changed

#### Scenario: Local-only workspace

- **WHEN** an open Local workspace has no binding or PuppyOne locator
- **THEN** Desktop renders the current workspace and one connect/backup action
- **AND** Desktop does not request or render Organization Projects

#### Scenario: Conflicting identity facts

- **WHEN** fetch/push remotes, multiple remotes, or binding and locator facts
  identify different origins, Projects, Scopes or kinds
- **THEN** Desktop fails closed to an actionable recovery state
- **AND** it does not select by iteration order, remote name or catalog order

#### Scenario: Explicit global browsing

- **WHEN** the user enters the global/home Cloud Projects browser outside an
  open Local workspace context
- **THEN** Desktop may enumerate Projects authorized for the current account
- **AND** that transient selection cannot override a later Local context

#### Scenario: Content operation after resolution

- **WHEN** clone, fetch, push or backup performs a Git content operation
- **THEN** the existing canonical locator plus machine credential resolves a
  RuntimeGrant and enters the existing Git adapter and Version Engine path
- **AND** the human context resolver is not a content-write path

#### Scenario: A context-resolution dependency is temporarily unavailable

- **WHEN** Project authorization facts or binding/Scope rows cannot be read
  consistently while resolving a binding or canonical locator
- **THEN** the backend fails closed with a generic retryable 503 rather than
  reporting that the Project, binding, Scope, or grant is absent
- **AND** one HTTP transport failure on a safe read is retried once, while a
  mutation is never automatically replayed
- **AND** Desktop preserves an exact context already verified under the same
  resolution key, or otherwise renders temporary-unavailability recovery
- **AND** Desktop does not enumerate Organization Projects or discard the
  durable local binding
