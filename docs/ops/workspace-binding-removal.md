# Server-side Checkout Identity Removal

Migration `20260716000000_remove_workspace_binding.sql` deletes the former
Workspace Binding schema and establishes user-owned Git credentials as the
final model.

## What is removed

- `project_workspace_bindings`;
- `access_surface_credentials.workspace_binding_id`;
- create, resolve, rotate, revoke, reconciliation, and trigger routines tied to
  that entity;
- Backend routes, capabilities, models, and repositories for it;
- Desktop persisted IDs, resolution states, recovery UI, and API calls for it.

## What replaces it

- canonical Git remote as the only local-to-Cloud locator;
- current JWT plus ProjectGrant for Cloud UI context;
- user-owned, hash-only Git credentials plus RuntimeGrant for Git transport;
- independent credential revocation by credential ID;
- local-only `workspaceInstanceId` with no Cloud representation.

## Backfill

Each active former checkout credential is assigned to its recorded human owner
and converted to lifecycle `user` when it is a Git HTTP credential. Hash,
Project, Access Surface, target, mode, and status are preserved. Credentials
whose owner no longer has Project access are revoked. Role downgrade is handled
dynamically by the runtime resolver and does not require rewriting the
credential.

The earliest registration implementation issued checkout-scoped CLI bearer
tokens. They cannot be mapped to a user Git principal without changing their
meaning or widening authority, so the migration deletes them. New CLI/shared
credential lifecycles are unaffected.

The migration aborts if ownership cannot be established or if postflight
integrity reports are non-zero.

## Finality rule

There is no optional old field, dual-read, dual-write, or fallback context
resolver. Historical migrations and archived specifications may describe the
removed schema solely so existing installations can be upgraded safely; active
runtime architecture must not depend on it.

## Verification evidence (2026-07-15)

- Migration shell syntax, SQL invariants, upgrade fixtures, architecture scans,
  and the complete non-external Backend test suite were run successfully.
- A disposable local Supabase database execution could not be completed on the
  validation workstation because Docker left the new Postgres container in
  `Created` and would not start it. No production or developer database was
  modified to work around that host failure.
- CI or staging MUST run `scripts/test-repository-target-migration.sh` against a
  real disposable Postgres instance before the migration is applied to a
  deployed environment.
