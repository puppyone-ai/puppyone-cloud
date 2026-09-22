## ADDED Requirements

### Requirement: Canonical remote is the sole workspace locator

Desktop MUST derive an open local workspace's Cloud target only from one trusted
canonical PuppyOne Git remote. Cloud MUST NOT persist or evaluate local folder,
checkout, or computer identity.

#### Scenario: GitHub-only workspace
- **WHEN** a local workspace has no canonical PuppyOne remote
- **THEN** Desktop renders local-only state without a Cloud API request or error banner

#### Scenario: Canonical PuppyOne remote
- **WHEN** a local workspace has one canonical PuppyOne remote
- **THEN** Desktop derives the exact origin and Project/root-or-Scope target from that URL
- **AND** no local Binding record or workspace instance is required

#### Scenario: Legacy secret-bearing remote only
- **WHEN** a local workspace has only a legacy PuppyOne transport URL
- **THEN** Desktop treats it as local-only for Cloud navigation
- **AND** sends no Cloud repository-context request

### Requirement: Project authorization is independent from Git authentication

Cloud UI MUST use the current human session and ProjectGrant. Git transport MUST
use a separate credential and RuntimeGrant. Neither path may substitute for the
other.

#### Scenario: Authorized Cloud context
- **WHEN** a signed-in user resolves a canonical Project locator
- **THEN** the backend authorizes current Project read access and returns context
- **AND** no Git credential or local workspace identity is inspected

#### Scenario: Git request
- **WHEN** stock Git sends a canonical target URL and user-owned Git credential
- **THEN** the server requires route target, credential target, active Surface,
  current user Project access, Scope geometry, and effective mode to agree
- **AND** it does not inspect a Binding or local folder identifier

### Requirement: User-owned Git credential lifecycle

The server MUST store user-owned Git credentials hash-only and MUST re-evaluate
the owning user's current Project access on every Git request.

#### Scenario: Role downgrade
- **WHEN** an Editor's Project role becomes Viewer
- **THEN** an existing user Git credential resolves at most read-only

#### Scenario: Membership loss
- **WHEN** the credential owner no longer has Project access
- **THEN** the next Git request fails closed without revealing target existence

#### Scenario: Independent revocation
- **WHEN** a user revokes one named Git credential
- **THEN** that credential stops resolving
- **AND** other credentials for the same user and target remain valid

## REMOVED Requirements

### Requirement: Explicit workspace binding

**Reason**: PuppyOne has no product requirement to register local checkouts or
computers, and stock Git cannot attest a local folder identity.

**Migration**: Existing Binding credential ownership is moved directly to
`access_surface_credentials.user_id`; the Binding table and client fields are
then deleted.
