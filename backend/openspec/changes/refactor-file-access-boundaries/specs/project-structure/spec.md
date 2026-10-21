## ADDED Requirements

### Requirement: File operations depend on Git primitives

Product operation orchestration SHALL belong to file_operations. Git tree
mutation algorithms and admitted read contexts SHALL belong to Version Engine.
Core read, write and storage implementations SHALL NOT depend on file_operations.

#### Scenario: History and file reads share snapshot handling
- **WHEN** history or Product operations open a native tree read
- **THEN** they use the same grant validation, snapshot lifetime, optional pinned
  reader and object-error mapping owned by the existing native tree reader module.

#### Scenario: Existing writers use the canonical owner
- **WHEN** a producer issues a file command
- **THEN** it imports the single file_operations implementation
- **AND** request digests, replay, grants and native ref publication are unchanged.

### Requirement: CLI filesystem transport belongs to Access

CLI AP-FS SHALL use an Access adapter with router, context, read, write and search
modules. Existing route contracts and filesystem semantics SHALL be preserved.

#### Scenario: CLI callers use existing HTTP endpoints
- **WHEN** a caller uses an existing AP-FS URL
- **THEN** authorization, arguments, errors, responses and operation semantics
  remain unchanged after the implementation moves.

#### Scenario: Upload and CLI share file rules
- **WHEN** an existing upload-policy consumer validates a file
- **THEN** it uses shared file_operations rules with unchanged limits and filters
- **AND** the move does not introduce policy enforcement at new entrypoints.

### Requirement: Shared credential ownership preserves protocol isolation

Credential hashing, persistence and lifecycle SHALL have one Access owner.
Protocol admission SHALL retain explicit credential types, target checks and
existing per-surface policies; shared ownership SHALL NOT imply token interchange.

#### Scenario: CLI and MCP retain different credential admission
- **WHEN** a CLI/Git token is presented to MCP or an MCP bearer token to CLI/Git
- **THEN** the existing type and surface checks reject the mismatched credential
- **AND** revoked or expired credentials stay rejected by their existing paths.

#### Scenario: Unavailable Scope remains closed
- **WHEN** a valid MCP credential or old Scope credential reaches the existing
  unavailable native Scope path
- **THEN** the existing rejection remains in effect after relocation.
