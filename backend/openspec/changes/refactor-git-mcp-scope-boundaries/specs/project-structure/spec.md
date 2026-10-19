## ADDED Requirements

### Requirement: Git transport belongs to Access

Git HTTP authentication, routing and wire transport SHALL belong to the Access
Git adapter. Version Engine SHALL NOT depend on Access adapters or Agent Runtime.
Run-bound orchestration SHALL belong to the existing Agent control module.

#### Scenario: A stock Git client exchanges repository data
- **WHEN** a client clones, fetches or pushes through an existing Git URL
- **THEN** the same protocol, admission, stream cleanup and publication contracts apply
- **AND** no new database, object-store or sandbox round trips are introduced.

#### Scenario: Internal maintenance uses shared primitives
- **WHEN** GC or workspace cleanup needs object facts, locks or cache metadata
- **THEN** it uses the core owner without importing HTTP transport or Agent code.

### Requirement: MCP and shared filesystem ownership are explicit

MCP runtime dispatch and credential resolution SHALL belong to its Access
adapter. Existing shared CLI/MCP filesystem operations and capability schemas
SHALL have one Access filesystem owner independent of protocol adapters.

#### Scenario: MCP tools are listed or invoked
- **WHEN** a request reaches an existing internal MCP runtime URL
- **THEN** the same internal-secret gate, bearer resolution, tool policy and dispatch apply
- **AND** native Scope unavailability remains an explicit rejection.

#### Scenario: CLI and MCP use shared filesystem behavior
- **WHEN** either adapter uses existing shared search or capability behavior
- **THEN** it calls the same implementation without importing the other adapter.

### Requirement: Scope geometry is independent of credentials

Scope geometry models, CRUD and repository persistence SHALL belong to
repository_target. Credential lifecycle and Scope credential resolution SHALL
belong to Access; project connection identity SHALL also belong to Access.

#### Scenario: A credential resolves to a Scope
- **WHEN** the existing legacy resolver validates a credential and target
- **THEN** token hashing, status, expiry, exact project binding and mode clamping are preserved
- **AND** repository_target does not depend on Access credentials or adapters.

#### Scenario: A human manages project Scopes
- **WHEN** a caller uses an existing Scope or project connection identity URL
- **THEN** the same human authorization, response schema and persistence behavior apply
- **AND** retired unused permission DTOs do not reintroduce a second authorization model.
