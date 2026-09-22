## ADDED Requirements

### Requirement: Canonical non-secret Git remote locators

PuppyOne MUST expose stable Project-root and scoped Git remote locators that
contain only non-secret Project/Scope identity and remain unchanged when a
credential rotates.

#### Scenario: Project root locator

- **WHEN** a client requests the canonical root Git remote for a Project
- **THEN** the locator is `/git/{project_id}.git`
- **AND** only the canonical root Scope can be served by that locator
- **AND** the locator contains no credential or binding secret

#### Scenario: Scoped locator

- **WHEN** a client requests a non-root Scope Git remote
- **THEN** the locator is `/git/{project_id}/scopes/{scope_id}.git`
- **AND** the declared Scope belongs to the declared Project
- **AND** the locator remains stable across credential rotation and Scope rename

#### Scenario: Ambiguous encoded locator

- **WHEN** a request percent-encodes any Project or Scope identifier character
- **THEN** the server rejects it instead of decoding a second textual locator
- **AND** no credential resolution or Project repository access occurs

### Requirement: Git credential separation

PuppyOne MUST authenticate canonical Git remotes with a secret supplied through
standard HTTP authorization and MUST NOT place a replayable credential in the
canonical URL.

#### Scenario: Missing Git credential

- **WHEN** stock Git requests a canonical remote without credentials
- **THEN** the server returns `401 Unauthorized`
- **AND** the response contains a standards-compliant Basic authentication
  challenge
- **AND** no Project or Scope metadata is disclosed

#### Scenario: Credential rotation

- **WHEN** an authorized actor rotates a Git runtime credential
- **THEN** the old credential is revoked
- **AND** the new plaintext is returned once
- **AND** the remote locator does not change

#### Scenario: Rotation-domain isolation

- **WHEN** an authorized actor rotates a shared `r` or shared `rw` credential
- **THEN** only the prior credential in that exact shared mode slot is revoked
- **AND** short-lived session, Workspace Binding, and opposite-mode shared
  credentials remain active

#### Scenario: Client persistence

- **WHEN** a first-party interactive client configures a canonical remote
- **THEN** it stores the credential through an OS-backed path-aware Git
  credential helper
- **AND** it stores only the non-secret locator in Git remote configuration

#### Scenario: Discovery responses

- **WHEN** a user reads Scope, Repo Identity, Access-list, or dashboard metadata
- **THEN** no replayable Git or CLI credential is returned
- **AND** any credential hint is treated only as presentation metadata
- **AND** a runnable setup command is built only from a dedicated one-time
  issuance response

### Requirement: Exact Git target resolution

Every canonical Git request MUST resolve one exact active Project, Scope, Git
Access Surface, credential, mode, and optional Workspace Binding before the
Version Engine opens Project state.

#### Scenario: Root URL with scoped credential

- **WHEN** a non-root Scope credential is presented to `/git/{project_id}.git`
- **THEN** authentication fails before Git refs, health, cache, or Project
  metadata are exposed

#### Scenario: Scoped URL credential mismatch

- **WHEN** a credential resolves to a different Project or Scope than the IDs
  declared by the scoped locator
- **THEN** authentication fails closed with no target-existence disclosure

#### Scenario: Successful RuntimeGrant

- **WHEN** the credential, Git Access Surface, Scope, Project, lifecycle state,
  mode, and optional Binding all match
- **THEN** L2 emits one immutable scope-bounded `RuntimeGrant`
- **AND** no human Project role is carried into the Git data plane

### Requirement: Credential-level Git mode

Each Git credential MUST have an `r` or `rw` ceiling independent of the Scope's
maximum mode, and effective authority MUST only narrow across all relevant
policy facts.

#### Scenario: Read-only key on read-write Scope

- **WHEN** an `r` credential targets a Scope whose maximum mode is `rw`
- **THEN** clone and fetch are allowed
- **AND** push is rejected

#### Scenario: Binding or role downgrade

- **WHEN** a Workspace Binding, Scope, or current human capability is downgraded
  below the credential's original mode
- **THEN** the next authenticated Git request uses the lower effective mode or
  fails closed

### Requirement: Version Engine invariance across locator families

Canonical and legacy Git route resolution MUST converge on the same RepoFacade,
Git-view cache, VersionSubmissionIntent, canonical-root publish, audit, and
readiness contracts.

#### Scenario: Credential rotation and cache identity

- **WHEN** two active principals or two rotated credentials resolve to the same
  Project, scope path, and excludes
- **THEN** they reuse the same derived Git-view cache identity
- **AND** no credential, binding, or route family enters the cache key

#### Scenario: Scoped push

- **WHEN** a push through a canonical scoped locator is accepted
- **THEN** the Version Engine validates the same path/exclude boundary as the
  equivalent legacy Access Point
- **AND** it publishes through the canonical Project root and source-Scope CAS
- **AND** the non-root transaction does not satisfy root Git readiness

### Requirement: Human control-plane and Git data-plane separation

PuppyOne MUST keep Git transport authentication machine-only while exposing
root Git-view diagnostics and repair to authorized human Project surfaces
through separate control-plane endpoints that reuse the same derived Version
Engine operations.

#### Scenario: Human reads root Git-view health

- **WHEN** a signed-in user with Project Read capability views Git health
- **THEN** the Web client calls the Project control-plane health endpoint with
  its human JWT
- **AND** the response is derived from the canonical root Scope
- **AND** the response states whether the current ProjectGrant may rebuild the
  cache
- **AND** the JWT is not forwarded to or accepted by a `/git` route

#### Scenario: Human rebuilds the root Git-view cache

- **WHEN** a signed-in user requests a root cache rebuild
- **THEN** Project Manage capability is required
- **AND** both full-history-with-blobs and receive-boundary-without-blobs cache
  variants are rebuilt from canonical facts
- **AND** a non-Admin receives `403` without invoking repair

#### Scenario: JWT presented to Git data plane

- **WHEN** a human JWT is presented to a locator-relative Git health or rebuild
  endpoint without a valid Git runtime credential
- **THEN** Git authentication fails uniformly
- **AND** no ProjectGrant is translated implicitly inside the transport route

### Requirement: Bounded legacy Git URL migration

PuppyOne MUST treat `/git/ap/{access_key}.git` only as an explicitly monitored
compatibility route and MUST retain it until supported first-party remotes have
migrated to canonical locators or a separately approved removal change is
deployed.

#### Scenario: Existing legacy remote conversion

- **WHEN** a first-party client converts an existing legacy remote
- **THEN** it resolves the credential to an authorized Project/Scope without
  logging the secret
- **AND** writes the credential to the credential helper
- **AND** verifies the canonical remote before removing the secret-bearing URL

#### Scenario: Legacy route retirement

- **WHEN** a legacy-route removal is proposed
- **THEN** removal requires a separate approved contract change
- **AND** first-party migration and zero-usage observation gates have passed
- **AND** the server has never redirected the secret-bearing route to the
  canonical route
