## ADDED Requirements

### Requirement: Explicit personal ownership
The system SHALL identify a personal Cloud destination by a durable owner binding, not by a name, default selection, creator or membership count. Existing unbound organizations SHALL remain team destinations and existing projects and access SHALL not be moved or revoked by this change.

#### Scenario: Existing team with one member
- **WHEN** its owner requests a personal destination
- **THEN** a separate personal space is created and the existing team and its projects remain unchanged.

#### Scenario: Concurrent requests
- **WHEN** two requests ensure the same user's personal destination
- **THEN** both receive the same organization with exactly one owner membership.

### Requirement: Personal admission
A personal destination SHALL admit only its bound owner and SHALL not be transferred, turned into a team, or removed through team management. Server-side publication admission SHALL still enforce the current membership and quota.

#### Scenario: Another member is invited
- **WHEN** an invitation or membership mutation targets a personal destination
- **THEN** the operation is refused and no new access is granted.

### Requirement: Cloud destination discovery
Authenticated organization responses SHALL expose personal/team kind and current-user project creation capability without exposing supplier details or credentials.

#### Scenario: Membership is revoked after discovery
- **WHEN** a user attempts publication to a previously discovered destination after their organization membership is revoked
- **THEN** the server refuses publication regardless of the client's cached creation capability.

#### Scenario: Existing project permissions
- **WHEN** an organization member may create a new project under the current admission policy
- **THEN** discovery reflects that policy without granting edit rights to other existing projects or silently redefining organization roles.
