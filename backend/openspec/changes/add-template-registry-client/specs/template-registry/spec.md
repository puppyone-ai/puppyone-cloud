## ADDED Requirements

### Requirement: Configurable Template Registry

The application SHALL resolve templates through an operator-configured
Registry provider and MUST support disabled, built-in, and remote modes without
requiring an official PuppyOne database or credential.

#### Scenario: Self-hosted Registry is disabled

- **WHEN** an operator configures the Registry as disabled
- **THEN** the catalog status reports that it is unavailable and no remote
  service is contacted

#### Scenario: Hosted deployment uses an external Registry

- **WHEN** an operator configures remote mode with a valid Registry URL
- **THEN** the backend obtains catalog and release data through the versioned
  Registry HTTP contract

### Requirement: Immutable Portable Release

Every remotely instantiated template MUST be represented by an immutable,
portable release bundle whose complete bytes and declared contents can be
verified independently of the source Project.

#### Scenario: Valid release is accepted

- **WHEN** the ZIP, manifest, file inventory, sizes, digests, and required
  signature all validate
- **THEN** the importer returns the exact declared file map

#### Scenario: Unsafe release is rejected

- **WHEN** an archive contains traversal, an absolute path, a symlink, a
  duplicate or undeclared entry, a secret-like path, excessive data, or a
  digest/signature mismatch
- **THEN** the release is rejected before a destination Project is created

### Requirement: Independent Project Instantiation

Using a template SHALL create one new Project owned by the selected
Organization and SHALL write the release content as a fresh Version Engine
commit with no continuing relationship to the source.

#### Scenario: Template is instantiated

- **WHEN** an authorized user selects a valid release and has Project capacity
- **THEN** the system creates a new Project and writes only the release files in
  one initial template commit

#### Scenario: Content write fails

- **WHEN** destination provisioning succeeds but the release write fails
- **THEN** the system attempts to delete the partial Project and reports the
  failure without returning a usable destination

### Requirement: One Application API Across Clients

Web, Desktop, and CLI clients MUST consume the canonical application Template
API rather than connecting directly to Registry storage or its database.

#### Scenario: Client browses and uses a template

- **WHEN** a client lists templates, opens a detail, or instantiates a release
- **THEN** it calls `/api/v1/templates` endpoints and receives the same typed
  catalog and destination Project contracts

### Requirement: Backward-Compatible Built-In Templates

Existing built-in template IDs and legacy Project template routes SHALL remain
functional during migration to the canonical Registry API.

#### Scenario: Legacy client lists templates

- **WHEN** a legacy client calls `/api/v1/projects/templates/list`
- **THEN** it receives the active provider's template summaries without needing
  a database migration
