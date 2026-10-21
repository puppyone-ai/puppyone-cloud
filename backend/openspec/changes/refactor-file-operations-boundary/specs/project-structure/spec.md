## ADDED Requirements

### Requirement: Hosted file operation command ownership

Hosted file command implementations SHALL belong to `src/file_operations`.
This package is a protocol-neutral operation module and SHALL NOT require its
own HTTP router, database repository or duplicate Git publication service.

#### Scenario: Existing producer imports remain valid
- **WHEN** a caller imports the command service or public native-command helpers
  through `src.version_engine.adapters.product`
- **THEN** the old path exports the same objects as `src.file_operations`
- **AND** only the new location contains their implementation.

#### Scenario: Frontend write assembly uses the new owner
- **WHEN** the existing content write route obtains its command service
- **THEN** the service implementation belongs to `src.file_operations`
- **AND** the URL, authorization, response and publication path are unchanged.

#### Scenario: Operation behavior survives the source move
- **WHEN** a producer saves, moves, copies or deletes a hosted file
- **THEN** normalization, byte paths, request identity and pinned-tree checks
  retain their existing semantics
- **AND** publication continues through the existing Product adapter and native
  Git transaction boundary without extra I/O.
