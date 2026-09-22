## ADDED Requirements

### Requirement: Canonical Project authorization

The system MUST resolve every human Project action through one canonical policy
decision point and MUST deny unknown roles, actions, inconsistent tenant facts,
or policy repository failures.

#### Scenario: Organization-visible default

- **WHEN** an Organization member has no explicit membership in an
  Organization-visible Project
- **THEN** the effective Project role is Viewer
- **AND** no mutation or credential-management capability is granted

#### Scenario: Private Project isolation

- **WHEN** an Organization member has no explicit membership in a private Project
- **THEN** Project metadata and child resources are not returned

#### Scenario: Explicit and owner roles

- **WHEN** an Organization owner accesses any Project in that Organization
- **THEN** the effective Project role is Admin from the Organization-owner source
- **WHEN** a non-owner has an explicit Project role
- **THEN** that explicit role is used instead of the Organization-visible baseline

### Requirement: Action-based enforcement

Project-scoped code MUST authorize named actions against immutable capabilities
and MUST NOT use truthy roles or runtime credentials as human authorization.

#### Scenario: Viewer attempts mutation

- **WHEN** a Viewer invokes any Project, content, member, Access, Agent, MCP,
  Sandbox, or credential mutation not present in Viewer capabilities
- **THEN** the request is denied before business mutation code runs

#### Scenario: Machine principal reaches control plane

- **WHEN** a Git, CLI, Agent, MCP, Sandbox, or binding credential is presented to
  a human Project, Team, Billing, member, or credential-management endpoint
- **THEN** the request is denied

#### Scenario: Public disclosure remains a Project Admin action

- **WHEN** a user creates, lists, updates, or deletes a public Publish link for
  Project content
- **THEN** the service re-resolves the current `share.manage` Project action
- **AND** old resource ownership cannot preserve management access after the
  user's Project grant is revoked
