# Change: Remove server-side Workspace Binding identity

## Why

PuppyOne has no product requirement to register local folders or computers in
Cloud. Git already supplies the complete workspace-link contract: the canonical
remote locates a Project/root-or-Scope target, while a separate credential
authenticates and bounds the Git request without identifying a computer or
folder. `project_workspace_bindings`
duplicates that identity, cannot attest which local folder originated a Git
request, and creates split-brain state between Git config, local JSON, and the
server.

## What Changes

- **BREAKING** Delete `project_workspace_bindings`, Binding RPCs/APIs, Binding
  capabilities, local `bindingId`, and all Binding-driven Desktop states.
- Make a trusted canonical PuppyOne Git remote the sole workspace-to-Cloud
  locator. No PuppyOne remote means local-only.
- Replace Binding credentials with user-owned, Project/target-scoped Git
  credentials. Git admission continues to re-evaluate current Project access in
  one database snapshot.
- Resolve Cloud UI context with the current JWT and ProjectGrant independently
  of Git credentials.
- Keep `workspaceInstanceId` only as Desktop-local workspace/cache identity; it
  is never persisted or evaluated by Cloud.
- Remove obsolete tests/documentation and add architecture guards that reject
  reintroduction of Workspace Binding identity.

## Impact

- Affected specs: repository targets, Git remote transport, Project
  authorization, Desktop contextual resolution.
- Affected code: Supabase schema/RPCs, machine credential resolution, Backend
  routers/services, Desktop config and Cloud state, migration/runbook docs.
