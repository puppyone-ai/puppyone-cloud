# Change: Refactor canonical Git remote locators and credentials

Status: **approved, implemented, and verified in the working tree; not deployed
or archived**

## Why

PuppyOne's legacy scoped Git URL embeds a replayable bearer credential in
`/git/ap/<access_key>.git`.  That makes the secret double as routing identity,
prevents clients from determining the owning Project without resolving the
secret, changes the remote URL whenever a key rotates, and exposes credentials
to Git config, command history, proxy logs, screenshots, and copied links.

The backend already exposes a Project locator at `/git/{project_id}.git`, while
the Version Engine already models every Git target as a Project plus a root or
non-root Scope.  The public Git contract should reflect those durable facts and
carry authorization separately through standard Git HTTP credentials.

## What Changes

- Define two stable, non-secret Git remote locators:
  - Project root: `/git/{project_id}.git`
  - Scoped view: `/git/{project_id}/scopes/{scope_id}.git`
- Move Git secrets out of URL paths and into standard HTTP Basic credentials,
  with OS-backed Git credential-helper storage for first-party clients.
- Resolve every Git request to one exact `RuntimeGrant` by validating URL
  Project/Scope facts against the credential, Access Surface, Scope geometry,
  lifecycle state, and optional Workspace Binding.
- Treat the root Scope as the Project-level Git target instead of introducing a
  second Project-key authorization model.
- Add credential-level `r`/`rw` ceilings so multiple credentials for the same
  Scope can have different authority.
- Keep `/git/ap/{access_key}.git` as a bounded migration route, then remove it
  only after all first-party clients and stored remotes have moved.
- Let Desktop use a trusted canonical locator as deterministic Project/Scope
  discovery while preserving Workspace Binding as durable local identity and
  ProjectGrant as human authorization.
- Preserve the Version Engine's RepoFacade, Git-view cache, official Git
  transport, canonical-root CAS, scope projection, audit, and readiness
  semantics unchanged after L2 resolution.

## Impact

- Affected specs: `git-remote-transport`, `workspace-binding`
- Depends on active changes:
  - `replace-handrolled-git-transport-with-official-git-backend`
  - `harden-machine-credential-storage`
  - `refactor-unified-authorization-boundaries`
- Affected backend:
  - `src/version_engine/entrypoints/git`
  - `src/version_engine/admission`
  - `src/repo/access_credentials.py`
  - `src/repo/access_surface_repository.py`
  - Workspace Binding issuance and resolution
  - route authorization manifest
- Affected schema: `access_surface_credentials` credential mode, explicit
  shared/session/binding lifecycle domain, and Git credential/surface integrity
- Affected clients: Desktop, Web connect surfaces, CLI instructions, Sandbox
  bootstrap, and real-Git test harnesses
- Affected documentation:
  - `docs/architecture/05-git-remote-accesspoint.md` (normative owner)
  - `docs/architecture/12-project-authorization-and-workspace-binding.md`
  - `docs/architecture/01-version-engine.md`
  - `docs/architecture/06-gateway-access-point-split.md`
  - `docs/architecture/03-cli.md`
- Compatibility: additive rollout first; removal of the legacy secret-bearing
  route is a separately gated breaking change
