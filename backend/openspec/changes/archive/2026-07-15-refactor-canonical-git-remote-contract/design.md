# Design: Canonical Git remote locators and runtime credentials

Status: **approved and implemented in the working tree; deployment/archival is
separately gated**

## Status and document ownership

This document is the reviewed design record for the implemented contract.
Runtime deployment follows the additive rollout gates; OpenSpec archival waits
until deployment rather than being used as evidence that deployment occurred.

When implemented, documentation authority is intentionally split as follows:

| Document | Owns | Must not duplicate |
| --- | --- | --- |
| `docs/architecture/05-git-remote-accesspoint.md` | Normative Git URL grammar, HTTP credential transport, target resolution, Git protocol behavior, compatibility route | Human roles and the full Version Engine design |
| `docs/architecture/12-project-authorization-and-workspace-binding.md` | Separation of ProjectGrant, RuntimeGrant, canonical locator discovery, and WorkspaceBinding | Git pack/CAS/cache mechanics |
| `docs/architecture/01-version-engine.md` | L1-L6 flow, RepoFacade, Git view, canonical-root publish and repair invariants | Client credential storage and URL migration details |
| `docs/architecture/06-gateway-access-point-split.md` | Concise gateway/control-plane boundary | A second Git protocol specification |
| `docs/architecture/03-cli.md` | User-facing commands after rollout | Architectural authority |

`05` is therefore the single source of truth for this mechanism.  Other
documents link to it and restate only the invariant needed in their own layer.

## Context

PuppyOne currently exposes two Git route families:

```text
/git/{project_id}.git
/git/ap/{access_key}.git
```

The first contains a durable Project locator but currently accepts credentials
whose resolved Scope is not necessarily root.  The second resolves a Scope but
places the raw bearer token in the path, so a client cannot determine Project
identity without authenticating the secret and key rotation changes the remote
URL.

The Version Engine does not need either wire shape.  After identity resolution
it consumes a Project/Scope-bounded view:

```text
RuntimeGrant
  -> RepoFacade(project_id, scope_id/path, excludes, mode, ref)
  -> GitViewHead and transport cache
  -> upload-pack or receive-pack
  -> VersionSubmissionIntent
  -> canonical Project root CAS
```

That separation allows the public locator and the secret credential to be
designed independently without changing L3-L6 semantics.

## Goals

- Make every first-party Git remote stable, non-secret, and deterministically
  attributable to one Project and either its root or one exact Scope.
- Use one credential and RuntimeGrant pipeline for root and scoped Git targets.
- Make target confusion fail closed before any Project repo, ref, cache, or
  metadata is opened.
- Keep credential rotation independent from locator identity and Git-view cache
  identity.
- Support distinct read-only and read-write credentials for the same Scope.
- Preserve stock Git smart-HTTP behavior and all Version Engine correctness
  invariants.
- Give Desktop a deterministic Project/Scope candidate without turning a Git
  remote or machine token into human authorization or durable local identity.
- Migrate first-party clients without a flag-day outage or silent secret leak.

## Non-goals

- Do not make Project IDs or Scope IDs secret capabilities.
- Do not let a Git credential call Team, Billing, Project settings, membership,
  sharing, or credential-management APIs.
- Do not introduce a second physical repository or object store per Scope.
- Do not encode Project, Scope, role, or permissions into a self-describing key.
- Do not change branch, tag, force-push, LFS, conflict, or root-first Version
  Engine policy in this change.
- Do not use scope names or filesystem paths as stable URL identity.
- Do not silently treat any arbitrary Git remote as a Workspace Binding.

## Terminology

### Git Remote Locator

A stable, non-secret HTTPS URL that declares the requested Project and target
kind.  It is safe to persist in `.git/config` and a PuppyOne manifest.

### Git Runtime Credential

An opaque, high-entropy, one-time-revealed secret.  The server stores only an
HMAC hash plus non-secret metadata.  It is transported as an HTTP Basic
password or Bearer token and never appears in a locator.

### Access Surface

The protocol/policy entry point.  A Git credential resolves through an active
`access_surfaces(kind='git_remote')` row to exactly one Scope.

### Scope

The canonical data-plane geometry: Project, path, excludes, maximum mode, and
root/non-root identity.  The root Scope is the Project-wide Git view.

### RuntimeGrant

The immutable L2 result consumed by Version Engine admission.  It carries a
machine principal, Project, Scope, path, excludes, effective mode, and optional
surface policy.  It carries no human Project role.

### Workspace Binding

The stable, revocable fact that a local workspace instance represents one
Cloud Project/Scope.  A locator can discover a candidate; only a binding is the
durable local identity.

## Canonical locator contract

### Root Project remote

```text
https://<git-origin>/git/{project_id}.git
```

This route means the canonical root Scope.  A credential resolving to any
non-root Scope is rejected, even when it belongs to the same Project.

### Scoped remote

```text
https://<git-origin>/git/{project_id}/scopes/{scope_id}.git
```

This route means the exact non-root Scope identified by `scope_id`.  Both IDs
are present intentionally:

- Desktop can identify the Project without a Project list or credential scan.
- The server can prove the Scope belongs to the declared Project.
- A credential for a sibling Project or sibling Scope cannot be replayed at
  this route.
- Scope renames do not change the remote because the ID, not name/path, is the
  locator.

The route does not imply a physical Git repository.  It selects a virtual
RepoFacade over the shared Project object store.

### Smart-HTTP suffixes

Both locator families expose the same suffixes:

```text
GET  <locator>/info/refs?service=git-upload-pack
GET  <locator>/info/refs?service=git-receive-pack
POST <locator>/git-upload-pack
POST <locator>/git-receive-pack
GET  <locator>/health
POST <locator>/rebuild-cache
```

These suffixes remain RuntimeGrant-protected machine data-plane operations.
The signed-in Web application uses root-only Project control-plane adapters at
`GET /api/v1/projects/{project_id}/git-view/health` and
`POST /api/v1/projects/{project_id}/git-view/rebuild-cache`, authorized as
Project Read and Project Manage respectively. The adapters call the same
derived health/cache implementation but never teach Git transport auth to
accept a human JWT. Health includes an explicit `can_rebuild` fact derived from
the current ProjectGrant.

No target identity is accepted from a query parameter or request body.

### Identifier rules

- Project and Scope IDs are opaque non-secret identifiers.
- The router applies strict length/character validation before repository
  access.
- Scope names and paths never appear in the canonical URL.
- The URL contains no credential ID, raw credential, role, mode, email, user
  ID, binding ID, or local path.

## Credential delivery contract

The recommended Git HTTP form is:

```text
username: x-puppyone-token
password: <one-time Git runtime credential>
```

Stock Git sends it as HTTP Basic authorization.  Bearer remains an accepted
programmatic transport when a client can set the header directly.  Missing or
invalid authentication returns:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="PuppyOne Git"
```

The response must not reveal whether the Project, Scope, Surface, Binding, or
credential exists.

First-party interactive clients store the credential through the OS-backed Git
credential helper.  Because one host can serve multiple Project/Scope
credentials, clients set `credential.useHttpPath=true` for the PuppyOne remote.
They must not configure `credential.helper store`, embed `user:password@` in the
remote, or write `http.extraHeader` containing a long-lived token to `.git/config`.

Non-interactive Sandbox/worker clients use a short-lived credential delivered
through an ephemeral credential helper or process-scoped environment.  They do
not persist it in images, manifests, command arguments, or logs.

## Canonical credential relationship

All Git credentials use one relationship:

```text
access_surface_credentials
  -> access_surfaces(kind='git_remote', status='active')
  -> repo_scopes
  -> projects
```

There is no alternative direct `credential -> project` authorization path.
Project-level Git is represented by the same relationship pointing at the
canonical root Scope.

New Git credentials use `credential_type='git_http_token'`.  Legacy
`bearer_token` Scope credentials remain eligible only on explicitly documented
compatibility paths during migration.

The `bearer_token` used by PuppyOne FS CLI remains a separate rotation domain.
It is stored hash-only and revealed only by Scope create/regenerate. Ordinary
Scope, Repo Identity, Access list, and dashboard reads must return no plaintext;
masked hints are never accepted as setup credentials. Web keeps a newly issued
CLI value in ephemeral component state and builds runnable commands only after
that explicit response. Rotating this compatibility/CLI token cannot rotate a
canonical Git credential.

Each credential carries a `grant_mode` ceiling:

```text
r  = clone/fetch/read only
rw = read plus accepted push/write operations
```

This permits simultaneous read-only and read-write credentials on one Scope.
The Scope's mode remains an upper bound, not a per-credential grant.

Each credential also carries an explicit `credential_lifecycle` revocation
domain: `shared`, `session`, or `binding`. Shared `r` and `rw` slots rotate
independently; a shared rotation cannot revoke an expiring session or a
per-workspace binding credential. A session requires an expiry and a binding
credential requires `workspace_binding_id`; database constraints enforce both
shapes.

Binding credentials additionally reference `workspace_binding_id`.  Shared
service credentials have no binding and are explicitly created, expired,
rotated, revoked, and audited by a Project Admin.

## L1-L3 target resolution

Resolution is one fail-closed operation.  Production should implement it as a
repository join or transactional RPC rather than a sequence of independently
trusted lookups.

### Step 1: Parse declared target

The route yields:

```text
GitRouteTarget(
  project_id,
  scope_id = null | exact id,
  target_kind = root | scoped,
)
```

### Step 2: Extract credential

The Git adapter extracts the Basic password or Bearer value.  It never accepts
the route, username, Project ID, Scope ID, Git actor header, or user-supplied
scope query as proof of authorization.

### Step 3: Resolve active credential facts

The server HMAC-hashes the presented secret and loads, in one bounded fact set:

```text
credential status / expiry / grant_mode / binding relation
surface project / scope / kind / status / channel policy
scope project / path / excludes / mode / is_root
binding project / scope / user / mode / status (when present)
current ProjectGrant capability (only for a binding credential)
```

### Step 4: Match all identities

The request is rejected unless all relevant equality checks hold:

```text
credential.project_id == route.project_id
surface.project_id    == route.project_id
scope.project_id      == route.project_id
surface.scope_id      == scope.id
surface.kind          == git_remote
surface.status        == active
```

Root additionally requires:

```text
scope.is_root == true
scope.path == ''
route.scope_id is null
```

Scoped additionally requires:

```text
scope.is_root == false
scope.id == route.scope_id
```

When a binding is present, its Project, Scope, user, origin, status, and mode
must match the credential facts and its current human Project capability must
still permit the requested binding mode.

### Step 5: Calculate effective mode

Authority can only narrow:

```text
effective_mode = minimum(
  scope.mode,
  credential.grant_mode,
  binding.mode                 if binding-backed,
  current human capability     if binding-backed,
  active surface/channel policy,
)
```

Any unknown value, repository failure, tenant mismatch, expired credential,
revoked binding, disabled surface, or unsupported credential type fails closed.

### Step 6: Produce RuntimeGrant

Only the resolved immutable grant crosses into admission:

```text
RuntimeGrant(
  principal,
  project_id,
  scope_id,
  path,
  excludes,
  mode,
  tools/policy,
)
```

Git routes do not pass a raw `ProjectGrant` downstream.  If the product later
supports short-lived human-session Git, L2 must exchange or translate it into a
bounded RuntimeGrant before admission.  A human JWT is never accepted as a
substitute for machine target resolution.

Git usernames and actor headers remain client-controlled attribution only.
Audit facts additionally record the server-resolved RuntimeGrant principal,
credential kind, Access Surface, and optional Workspace Binding so authorization
identity cannot be forged by changing the Basic username.

## Version Engine compatibility

The change stops at the L2/L3 boundary.

| Layer | Contract after this change |
| --- | --- |
| L1 Git protocol | New locator grammar and standard Authorization extraction |
| L2 identity | Exact Project/Scope/surface/credential resolution to RuntimeGrant |
| L3 admission | Existing RepoFacade, mode, excludes, pause and action checks |
| L4 Git adapter | Existing official upload-pack/receive-pack and quarantine path |
| L5 core | Existing VersionSubmissionIntent, path validation, scope-head and root CAS |
| L5 follow-up | Existing scope projection, outbox, notifications and cache repair |
| L6 storage | Existing Project-shared Git object storage and derived transport cache |

### Git view cache identity

Credential, route family, binding, user, and Scope ID do not enter the Git view
cache key.  The cache remains a content-view identity:

```text
project_id
+ scope_path
+ scope_excludes
+ projection_version
+ history_mode
+ blob_mode
+ object_store_namespace
```

Therefore credential rotation does not invalidate content caches and multiple
authorized principals viewing the same Scope reuse one derived transport view.

### Canonical root and scoped pushes

The resolved Scope path and excludes continue to drive receive-pack admission.
A scoped push splices only its admitted subtree into the canonical Project root,
publishes through root CAS, preserves the accepted client commit as the source
Scope head, and refreshes affected derived views.  The locator does not create a
physical repository, independent source of truth, or bypass publish policy.

### Readiness

Root Git readiness remains derived from a committed transaction where:

```text
scope_path = ''
source_channel = 'access_git'
```

A scoped locator always resolves to a non-root path and cannot unlock Project
Claude/Agent readiness.

## Workspace Binding and Desktop navigation

A canonical locator is a deterministic discovery fact, not durable local
identity and not human authorization.

Desktop open flow:

```text
read secret-free PuppyOne remote
  -> require configured trusted Cloud origin
  -> parse project_id and optional scope_id
  -> read local workspaceInstanceId and bindingId
  -> authenticate current human session
  -> resolve active Workspace Binding when present
  -> verify ProjectGrant and exact Project/Scope match
  -> open the Project or scoped Project surface directly
```

If a valid binding exists, Desktop must not load the Organization project list
before navigating to the bound Project.

The Local-workspace session-restore path therefore disables automatic Project
catalog loading. Catalog enumeration is allowed for home/Cloud-only contexts,
or for an explicit browse action only when there is no binding or canonical
target candidate. Exact binding resolution and broad catalog loading must not
race.

If local binding state is absent but a canonical locator exists, Desktop may
use it as one Project/Scope candidate after JWT authorization.  It must not scan
all Projects, compare shared keys, or treat the remote as an already-created
binding.  The attach flow creates or recovers one binding explicitly and then
persists only origin, Project ID, Scope ID/kind, binding ID, and workspace
instance ID.

Legacy `/git/ap/<key>.git` discovery remains confirmation-gated because its
secret-bearing path does not provide a trusted non-secret locator contract.

## Issuance API contract

Binding or Git Access issuance returns the locator and one-time secret as
separate fields:

```json
{
  "remote": {
    "url": "https://host/git/project-id/scopes/scope-id.git",
    "project_id": "project-id",
    "scope_id": "scope-id",
    "kind": "scoped",
    "username": "x-puppyone-token"
  },
  "credential": "pwg_...",
  "credential_expires_at": null
}
```

Root omits `scope_id` from the URL but may include the canonical root Scope ID
as response metadata.  Ordinary reads return only prefix/last-four/status/
expiry metadata and the stable locator.  Rotation returns a new plaintext once
and the same locator.

No client constructs a credential-bearing URL by string replacement.  The
server is the sole authority for canonical locator construction.

## Error and disclosure contract

- Missing, invalid, expired, revoked, wrong-Project, wrong-Scope, wrong-kind,
  and mismatched-binding credentials return one generic authentication failure.
- An `rw` operation with an otherwise valid `r` grant returns a permission
  failure through the normal Git protocol outcome.
- Private Project/Scope names, paths, membership, binding metadata, and content
  are never disclosed before authorization.
- Project and Scope IDs may be present in the request path because they are
  locators, not capabilities; structured logs still use redacted references.
- Authorization headers, raw credentials, legacy secret paths, command
  arguments, and credential-helper payloads are always redacted.
- Every denial and accepted Git operation carries a request ID and a one-way
  Project/Scope reference for correlation.

## Performance contract

- Route parsing is local and constant-time.
- Credential lookup uses the HMAC hash index.
- Project/Scope/surface/binding validation should be one repository query/RPC.
- The resolver returns all immutable facts needed by RepoFacade; L3 does not
  re-fetch target identity.
- Request-scoped memoization is allowed; cross-request human-role caching is
  not.
- Credential rotation must not rebuild or invalidate Git view caches.

## Legacy migration

### Phase 1: Additive backend

- Add canonical scoped routes and strict root target checks.
- Add standard Basic challenge behavior.
- Add Git credential mode, explicit shared/session/binding lifecycle domains,
  and surface integrity.
- Keep `/git/ap/{access_key}.git` operational and instrument only redacted route
  usage counts.

### Phase 2: New issuance and first-party clients

- All new root/scoped issuance returns the canonical locator and separate
  credential.
- Desktop stores credentials in the OS credential helper and enables path-aware
  matching.
- Web UI, CLI instructions, Sandbox provisioning, and internal jobs stop
  generating secret-bearing URLs.

### Phase 3: Existing remote conversion

For an existing first-party `/git/ap/<key>.git` remote, an explicit migration
flow:

1. extracts the key locally without logging it;
2. resolves it to Project/Scope through the bounded legacy resolver;
3. verifies the current human ProjectGrant when a human session exists;
4. writes the secret to the credential helper;
5. rewrites the remote to the canonical locator;
6. verifies `git ls-remote` against the new URL;
7. removes the key-bearing URL from local config only after verification.

The server must not redirect the old secret-bearing URL to the new route:
redirects risk forwarding or logging credentials and produce inconsistent Git
credential behavior.

### Phase 4: Retirement gate

The legacy route can be removed only when:

- every first-party release generates canonical locators;
- migration telemetry reports no first-party legacy requests for the agreed
  observation window;
- stored configuration scans find no first-party secret-bearing remotes;
- rollback no longer requires the old path;
- an explicit contract migration/removal change is approved.

## Rollback

Before legacy route removal, rollback keeps both URL families and switches new
issuance back only if necessary.  Canonical routes are additive and do not
change Version Engine facts, so they can remain deployed even when clients roll
back.  Newly issued credentials remain hash-only and must never be re-embedded
into URLs during rollback.

After legacy route removal, rollback is forward repair: restore route handling
only from the reviewed compatibility implementation, not by replaying plaintext
credentials from logs or database state.

## Alternatives considered

### Keep `/git/ap/<access_key>.git`

Rejected because it conflates locator and secret, leaks credentials, breaks
stable identity on rotation, and forces Project discovery through a secret.

### Encode Project/Scope claims into the key

Rejected because self-describing long-lived Git tokens complicate revocation,
invite parsing as authorization, and still leak when placed in a URL.

### Use only `scope_id` in the URL

Rejected because Desktop cannot identify the owning Project without another
lookup and the route lacks a redundant Project/Scope integrity assertion.

### Use scope path or name

Rejected because paths/names are mutable presentation and filesystem facts,
need complex escaping, and may disclose tenant content structure.

### Use one opaque remote ID

Rejected for the primary contract because the product explicitly needs direct
Project discovery.  An opaque Access Surface ID can remain internal metadata,
but it is not the canonical first-party locator.

### Put the key in URL userinfo

Rejected because `https://user:key@host/...` still persists and leaks the
secret, and modern clients/proxies handle userinfo inconsistently.

## Verification matrix

The implementation must include real stock-Git coverage for:

- root clone/fetch/push with a valid root `rw` credential;
- scoped clone/fetch/push with exact Project/Scope matching;
- root URL with non-root credential denied;
- scoped URL with root, sibling-Scope, or sibling-Project credential denied;
- read-only clone allowed and push denied;
- expired, revoked, disabled-surface, revoked-binding, role-downgrade, and
  scope-downgrade denial;
- Basic challenge and OS/path-aware credential helper behavior;
- credential rotation preserving the locator and Git-view cache identity;
- no credential in remote URL, manifest, structured logs, exception messages,
  process arguments, or ordinary API reads;
- scoped push retaining existing nested-Scope/exclude/CAS behavior;
- scoped push not satisfying root readiness;
- canonical locator plus valid binding opening the Project without a Project
  list scan;
- safe legacy conversion with verification before secret-bearing URL removal;
- old and new routes producing equivalent RepoFacade and Version Engine facts
  during the compatibility period.

## Documentation promotion

After approval and implementation:

1. Promote the proposed contract in `05` to current status and reconcile it
   against the shipped route, storage, and migration behavior.
2. Promote the canonical-locator refinement already documented in `12` while
   preserving the rule that bindings identify local workspaces, runtime
   credentials authorize only RuntimeGrant, and JWT authorizes humans.
3. Reconcile the L1/L2 and Access Point summaries already staged in `01`.
4. Reconcile the compact gateway boundary already staged in `06`.
5. Update commands in `03`, frontend copy, and root contributor instructions.
6. Archive obsolete secret-bearing examples so search does not present them as
   current usage.

## Open questions

None at the architecture boundary.  Exact OS credential-helper adapters are a
Desktop implementation detail as long as they satisfy the storage and
path-isolation contract above.
