# Project-Owned Repository Targets

The Project owns the canonical repository. The Project root is represented by
the Project itself; a Scope is an optional restricted view into that repository.

## Canonical hierarchy

```text
Organization                         ownership, members, billing
└── Project                          canonical repository + authorization root
    ├── Canonical Git Repository     objects, commits, refs
    │   ├── Root Repository View
    │   │   └── /git/{project_id}.git
    │   └── Scoped Repository Views
    │       └── /git/{project_id}/scopes/{scope_id}.git
    ├── repository_scopes            non-empty path/exclude/max_mode geometry
    ├── access_surfaces              product/runtime entry points
    └── access_surface_credentials   independent machine principals
```

There is no synthetic root Scope and no repository per Scope.

## Wire targets

```json
{ "kind": "project_root", "project_id": "project-1" }
```

```json
{
  "kind": "scope",
  "project_id": "project-1",
  "scope_id": "scope-docs"
}
```

Every route and RPC compares the path Project ID, target Project ID, Access
Surface Project ID, and Scope Project ID. Mismatch fails closed.

## Relational model

```text
projects.id
  ├── repository_scopes.project_id
  ├── access_surfaces.project_id
  │     └── access_surface_credentials.access_surface_id
  ├── project_members.project_id
  └── version/audit/control-plane facts

access_surfaces.scope_id NULL  -> Project root
access_surfaces.scope_id value -> one repository_scopes row in same Project
```

`repository_scopes.path` is non-empty, normalized, and unique within the
Project. `exclude` and `max_mode` narrow the view. Scope deletion cannot leave
orphan Surfaces or runtime state.

## One repository, multiple views

All writes converge through the same canonical Project object store and root
ref. A scoped Git view filters the visible tree, enforces path/exclude policy,
and maps admitted writes back into the Project root transaction. Cache keys use
effective content geometry, not credential, user, local checkout, or Scope ID
alone.

This provides GitHub-like Project cloning for the common case and optional
least-privilege views without duplicating history or creating competing sources
of truth.

## Access Surface and credential boundaries

Creating a Scope does not implicitly mint a secret. Enabling a repository
target creates or reuses the required Git/CLI Access Surfaces atomically. A
credential targets one such Surface and carries a capability ceiling. The
runtime resolver combines credential, Surface, Scope, and current principal
facts into a RuntimeGrant.

ProjectGrant remains independent: a Scope or credential never creates human
membership, and a canonical locator never proves authorization.

## Desktop behavior

- Project-root canonical remote resolves the whole Project.
- Scoped canonical remote resolves that exact Scope after Project authorization.
- No canonical PuppyOne remote is local-only and causes no Cloud request.
- Conflicting canonical targets require local Git repair.
- Legacy secret-bearing URLs are transport compatibility only and never Cloud
  discovery input.

Desktop persists no server-side checkout identity. Its workspace instance ID is
local-only.

## Integrity invariants

- exactly one Project owns every repository fact;
- root target is `(project_id, scope_id = NULL)`;
- every non-null Scope ID exists in the same Project;
- no Scope has an empty path;
- no credential material is stored on a Scope;
- no device, folder, or checkout identifier participates in authorization;
- authorization and integrity reports must contain only zero counts before and
  after migration cutovers.
