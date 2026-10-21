## ADDED Requirements

### Requirement: Hosted repository API ownership

Web/Desktop repository HTTP parsing, route composition, response mapping,
signed download handling and version notification transport SHALL belong to
`src/platform/access/adapters/api`. Existing Git read/publication and file
operation implementations SHALL remain behind their current service boundaries.

#### Scenario: Existing clients use the relocated API
- **WHEN** a client uses the existing content, repository, audit or WebSocket URL
- **THEN** the application mounts the handler from the Access API adapter
- **AND** its authorization, request/response and I/O contracts remain unchanged.

#### Scenario: Protocols reuse wire schemas
- **WHEN** Web, Git or CLI handlers need shared repository wire models
- **THEN** they import the same definitions from `src.platform.access.repository_schemas`
- **AND** neither Git nor CLI depends on the Web adapter to obtain these models.

#### Scenario: Retired route paths cannot regain callers
- **WHEN** runtime source imports are checked
- **THEN** no caller imports the relocated modules through `version_engine.entrypoints.http`
- **AND** file operations and Version Engine do not depend on the Web adapter.

#### Scenario: AP-FS remains independently owned during migration
- **WHEN** the existing CLI AP-FS endpoints resolve credentials and handle a request
- **THEN** they continue using their existing entrypoint and behavior
- **AND** their shared schema import does not require the Web adapter.
