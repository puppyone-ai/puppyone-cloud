## Context

The existing design treats a server-side Workspace Binding as durable identity
for a local checkout. Stock Git never sends a local path or workspace instance,
so that record cannot prove checkout provenance. Its only effective role is to
group a user, target, mode, and credential, all of which belong directly on the
credential/runtime-grant path.

## Goals / Non-Goals

- Goals:
  - one authoritative workspace locator: the canonical Git remote;
  - current-JWT Project authorization for Cloud UI;
  - user-owned Project/target-scoped credentials for Git transport;
  - no Cloud knowledge of a local folder or computer;
  - one final schema and client contract with no Binding compatibility path.
- Non-Goals:
  - device inventory, checkout registration, heartbeat, or per-folder audit;
  - changing canonical Project/Scope repository ownership;
  - changing Desktop-local workspace identity used for caches and window state.

## Decisions

### Git remote is the workspace link

Desktop derives `{origin, target}` only from one trusted canonical PuppyOne
remote. If none exists, the workspace is local-only. Local config is not an
alternate Cloud identity source. Desktop parses the URL locally; the Backend
accepts only a structured `RepositoryTarget`, never a local path or raw remote
URL.

### ProjectGrant is the UI authorization source

Desktop resolves the canonical locator with the current session. The backend
authorizes `ProjectAction.PROJECT_READ` before returning Project or Scope
metadata. Git credentials do not grant control-plane access.

### User Git credential replaces Binding credential

`access_surface_credentials` gains nullable `user_id`. A `user` lifecycle
credential requires that user, Project, Surface, target, and requested mode to
agree. Runtime resolution re-evaluates current Project role and caps effective
mode. Shared and expiring session credentials retain their existing independent
semantics.

Each user Git credential has its own ID and revocation endpoint. Multiple
credentials may coexist for the same user and target. Removing a local remote
does not revoke unrelated credentials. A local setup failure after issuance
stays in the durable publish journal and resumes with the same vault-backed
secret. Only explicit Abandon may revoke that operation credential, after the
server proves the same operation still owns an unpublished empty Project.
Revoking an owned credential remains allowed after Project access is lost,
because revocation is monotonic and ownership is checked directly.

Desktop main generates each user credential and persists it in the operating-
system vault before submitting it once for hash-only backend persistence. The
backend never generates or returns plaintext. Issuance is keyed by the publish operation's
UUIDv4 `Idempotency-Key`; exact retries return the original credential ID and
changed payloads fail closed, so an uncertain response cannot create duplicate
effective credentials.

### Project publication is a durable operation

Creating a Cloud Project is not compensated merely because the local Git setup
or the initiating HTTP response fails. The hidden `initializing` Project and
its UUIDv4 operation journal are resumed with the same idempotency key. An
explicit Abandon request may remove only the exact untouched bootstrap state;
the reconciler applies a durable deadline and bounded retries, then publishes a
deletion cleanup tombstone for a safe pre-root failure. Unexpected state is
dead-lettered for inspection rather than retried forever or exposed as a
half-created Project.

### No folder attestation

`workspaceInstanceId` remains local-only. It is not sent to Cloud, stored in a
Cloud table, or used in Git admission. If device-bound authentication is ever a
real product requirement, it requires a separate explicit design with actual
key possession/attestation, not a copied folder identifier.
Shared workspace config stores no local or Cloud Project ID. Recent-workspace
metadata may cache only a secret-free hint parsed from the canonical Git remote.

## Migration Plan

1. Add `access_surface_credentials.user_id` and backfill it from existing
   Binding credentials.
2. Convert credential lifecycle `binding` to `user`, revoke credentials whose
   owner no longer has Project access, retire historical checkout-scoped CLI
   bearer principals that cannot map to user Git semantics, and install
   user/tenant/mode constraints.
3. Replace runtime and issuance RPCs with user-credential versions.
4. Drop Binding functions, triggers, indexes, foreign keys, column, and table.
5. Deploy Backend and Desktop contract changes with the schema cutover; old
   Binding clients receive no compatibility response.

## Risks / Trade-offs

- A user may have multiple active Git credentials. That is acceptable and
  matches Git/PAT semantics; membership loss is checked on every request.
- Existing Desktop credentials remain usable after migration only when their
  former user still has sufficient current Project access.
- Removing a local remote does not need a Cloud mutation. Local credential
  helper cleanup may be retried locally; server-side authorization remains
  current and fail-closed, and server credential revocation is always an
  explicit operation.
