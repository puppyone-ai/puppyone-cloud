# Change: Unify registered-client access authorization and lifecycle

## Why

Hosted SSH currently owns a separate single-use grant while repository protocols
share only credential storage. A durable client identity and approved grant must
control all of its protocol credentials and sessions without granting human
Project administration or moving execution ownership out of Workspace.

## Authorization

The user approved the architecture in `puppy-issues:document/puppyone/architecture/04-authorization-model.md`
and explicitly requested this backend refactor on 2026-10-23. Implementation is
authorized; publication and hosted migration are separate operations.

## What changes

- Add durable registration, invitation, request, grant and access-session records.
- Reuse human authorization and protocol credentials; add common lifecycle
  admission and renewal with no legacy credential privilege expansion.
- Attach Hosted SSH to renewable parent authorization and exact Workspace
  execution identity; preserve its existing short-lived entry point.
- Enforce repository grant revocation at credential resolution and publication.
- Expose authenticated management and client renewal APIs. The user's follow-up
  on 2026-10-23 also authorizes the CLI registration/renewal connector and the
  invitation, approval and revocation UI using this same authority.
- Keep a full MCP OAuth authorization server separate from connector renewal.
- Preserve the explicit unsupported MCP native-Scope response; permission
  refactoring does not implement repository Scope or live MCP filesystem tools.

## Impact

Access/Authorization/Workspace boundaries, additive SQL migration, registered
client APIs, existing SSH/Git/CLI/MCP admission and their acceptance contracts.
