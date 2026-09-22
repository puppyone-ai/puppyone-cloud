# Project Authorization and Git Credentials

PuppyOne has two independent authorization planes. Human product actions use a
current `ProjectGrant`; machine data-plane actions use a bounded
`RuntimeGrant`. A Git remote only locates a target and grants nothing.

## Ownership and authority

```text
Organization
└── Project
    ├── project_members ------------ explicit human role facts
    ├── ProjectGrant --------------- current control-plane decision
    ├── Canonical Git Repository
    │   ├── Project root
    │   └── repository_scopes ------ optional non-empty path restrictions
    └── access_surfaces
        └── access_surface_credentials
            └── RuntimeGrant ------- data-plane decision
```

`project_members` is the sole explicit Project role source. Organization
membership provides tenant context; organization-visible Projects provide at
most the documented Viewer baseline. Child-resource rules may narrow a grant
but never create or widen Project access.

## ProjectGrant

All Project-scoped HTTP routes use
`platform.authorization.AuthorizationService` with a named `ProjectAction`.
The result is recomputed from current facts and registered in the route
authorization manifest.

Examples:

- reading Project content requires `content.read`;
- writing content requires `content.write`;
- issuing a Git credential requires current access to its Project target;
- revoking a user-owned credential requires exact Project, credential, and
  owner identity, but remains possible after Project access is lost;
- Team, Billing, membership, and settings remain human control-plane actions.

Project IDs, Scope IDs, Git locators, Runtime keys, ownership hints, and local
paths never create a ProjectGrant.

## Project publication lifecycle

Every production creation entry point uses the Project publication control
plane defined in [Project Publish Control Plane](16-project-publish-control-plane.md).
Authorization relies on one important consequence: a prepared Project is not a
published Project.

Publication has three distinct boundaries:

1. one database transaction serializes the explicit Organization, quota,
   idempotency key, and default-name allocation; it creates a hidden
   `initializing` Project, its creator Admin fact, and the durable operation;
2. the existing L5 `VersionWriteEngine.initialize_project_tree` boundary
   initializes the canonical root, after which an optional contentful
   initializer may run; and
3. a completion transaction verifies the root and changes lifecycle to
   `ready`.

The stable request fingerprint prevents one operation key from naming a
different user request. Resolved source metadata, such as a template release
and bundle digest, is persisted separately so a lost-response replay does not
depend on a mutable Registry alias, an unexpired landing ticket, or a retained
preview object.

`empty` root initialization is idempotently resumable. A `deferred` workflow
has one initializer owner and at most one initializer attempt; it is not
claimed to be exactly-once. A crash may leave partial side effects, which stay
hidden and are removed through the durable deletion closure rather than being
published as an empty substitute.

Ordinary product routes, authorization resolution, share-token joins, Git
credential issuance, and RuntimeGrant resolution require `ready`. The creator
Admin row already exists during preparation, but only the publication
coordinator, its initializer and completion path, the reconciler, and the
narrow abort/abandon control plane may address an initializing aggregate.

There is no lifecycle default and no legacy direct create API. The application
service role cannot directly insert or delete Projects or update
`lifecycle_status`; narrowly scoped `SECURITY DEFINER` functions own those
transitions. Ordinary metadata and L5 root updates remain an explicit column
allowlist.

Rows created before the lifecycle journal existed are backfilled to `ready`.
That is intentionally conservative: an old empty Project may be either an
abandoned attempt or a legitimate empty Project, and without a durable
`project_create_operations` row the system cannot prove which. The automatic
reconciler therefore acts only on journal-owned initialization rows. Ambiguous
legacy rows require an explicit operator decision; age, name, or empty refs are
never treated as ownership evidence.

Deletion similarly crosses one durable deletion control plane. Moving a
Project to `deleting` atomically closes renewable write-lease admission. A
worker drains every logical and physical writer before relational deletion,
then purges and independently verifies all persisted manifests: S3 objects and
multipart uploads, Turbopuffer namespaces, long-lived Scope sandboxes and
request execution sandboxes, plus durable external OCR/provider jobs and their
Redis/cache handles. Organization deletion waits for these authoritative jobs
to be `completed`, not merely for the Project row to disappear.

`WORKSPACE_BASE_DIR` and the Git view directory are different: they contain
non-authoritative, reconstructable derived caches. Every Git read and write is
admitted through the Project lifecycle fence, so after `deleting` no old cache
can be served and no new cache can be created. The deleting replica scrubs its
exact local paths best-effort, and every active replica repeats that scrub from
durable deletion tombstones on startup/each cleanup cycle. A local filesystem
check never claims global deletion completion. Hosted deployments must declare
this storage `ephemeral`; startup rejects persistent replica-local workspace or
Git cache storage until a shared/distributed cleanup adapter actually exists.

Legacy `users/{principal}/.../{project}/` storage requires a one-time resumable
inventory before deletion is enabled. The rollout scanner records principals,
cleans strictly parsed prefixes for already-deleted Projects, uses the real S3
object token and multipart `(KeyMarker, UploadIdMarker)` pair, and requires two
identical full-listing digests before opening the deletion gate. Run the
immutable `20260720_project_storage_inventory` data-migration artifact through
the protected migration workflow; it records, cleans, verifies, and publishes
a completion receipt before enabling deletion.

## RuntimeGrant

A machine entry point resolves an explicit target and capability ceiling:

```text
RuntimeGrant = {
  principal,
  project_id,
  target: ProjectRootTarget | ScopeTarget,
  path_prefix,
  excludes,
  effective_mode,
  policy
}
```

For user Git credentials the current Project role is checked on every credential
resolution. Membership removal denies immediately. Downgrading an Editor to a
Viewer caps existing credentials to read-only. Scope geometry and Surface policy
may reduce the mode further.

RuntimeGrant is intentionally unable to call human control-plane APIs. This is
the privilege firewall between cloned Git clients and organization management.

## Desktop Project context

Desktop does not persist Cloud identity for a local folder. It derives ephemeral
context from the actual canonical PuppyOne remote:

```text
canonical remote
  -> local parser extracts exact RepositoryTarget
  -> current JWT authorizes target Project
  -> backend validates exact Scope, if present
  -> secret-free Project context
```

The context endpoint accepts only the structured target. It does not accept raw
remote URLs or any device/folder/workspace identifier. Without a canonical
remote the workspace is local-only and no context request is sent.

## Credential lifecycle

User Git credentials are ordinary independently revocable credentials, similar
to scoped personal access tokens:

- the authenticated client generates plaintext with a CSPRNG; Desktop stores
  it in the OS credential vault, while the web UI keeps it only in memory for
  the one-time display;
- the issue request submits plaintext once for hashing, and no backend response
  returns it;
- only a keyed hash is stored;
- issuance is operation-key idempotent, so replay returns the original
  credential ID without creating a second effective credential;
- each credential has one user owner, one Project, one Access Surface, and one
  maximum mode;
- many credentials may coexist for different clients;
- revocation addresses the credential ID and owner, not a local folder;
- credential reads never recover plaintext;
- shared service keys and expiring session keys remain separate lifecycles.

The generic Access Surface `regenerate-key` route does not issue human Git
credentials. Git callers must use the idempotent Project credential endpoint;
the legacy Git branch returns `410 Gone`.

Local Git remote removal and server credential revocation are separate actions.
This avoids making the server infer a computer inventory from normal Git use.

For first-time Desktop publication, “shown exactly once” means delivered only
to the trusted Electron main caller. The Renderer never receives it. Electron
main generates the operation credential, protects it in an OS-backed vault,
and uses an idempotency key so a lost HTTP response can replay the same
hash-only credential outcome. The durable publish journal contains only an
opaque secret reference.

## Database constraints

`access_surface_credentials.user_id` is required for the `user` lifecycle and
must reference an organization member. A user credential must be a Git HTTP
credential. Shared and session credentials have no user owner; session
credentials require an expiry. Every credential must match its Project,
Organization, Access Surface, target, type, and mode constraints.

The schema intentionally has no table or foreign key for local checkout
identity.

## Required audit fields

Credential issue/revoke and Runtime authorization decisions record redacted
principal reference, Project, exact target, mode, credential type, outcome, and
reason. Never record raw JWTs, credentials, local paths, repository content, or
computer identifiers.
