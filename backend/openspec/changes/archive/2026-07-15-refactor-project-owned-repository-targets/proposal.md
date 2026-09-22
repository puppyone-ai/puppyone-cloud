# Change: Refactor Project-owned repository targets

Status: **approved by the user for implementation on 2026-07-15**

## Why

PuppyOne already has one canonical object store and history per Project, but its
database and API represent the Project-wide Git target as a synthetic root row
in `repo_scopes`. That forces root requests to carry an internal Scope ID and
duplicates target identity across `scope_id`, `is_root`, and `binding_kind`.

The final product model is simpler: the Project is the repository root; a Scope
is only a real, non-empty path boundary over that Project. Access surfaces,
workspace bindings, credentials, and runtime grants must preserve that
distinction without weakening Project-first authorization.

## What Changes

- **BREAKING** Rename `repo_scopes` to `repository_scopes`, retain only real
  non-root scopes, and delete the synthetic root rows and `is_root` column.
- **BREAKING** Represent root targets in `access_surfaces` and
  `project_workspace_bindings` with `scope_id IS NULL`; retain a composite
  `(scope_id, project_id)` foreign key for non-root targets.
- **BREAKING** Delete persisted/public `binding_kind`, root `scope_id`, and
  `root_scope_id`; use a discriminated `project_root | scope` target contract.
- Introduce typed `RepositoryTarget` and `ResolvedRepositoryView` boundaries
  for control-plane resolution and machine RuntimeGrant admission.
- Keep the canonical URL grammar, hash-only credentials, single-snapshot
  credential resolution, ProjectGrant separation, one Project object store,
  root-first CAS, and scoped projection behavior.
- Separate Scope lifecycle from Access Surface lifecycle; creating a Scope no
  longer implies a Git or CLI credential.
- Update Web and Desktop to consume the target union and remove root-Scope
  normalization/fallback behavior.
- Ship one gated cutover with preflight and postflight checks. The final source
  tree has no dual read, dual write, old DTO fallback, or compatibility view.

## Supersedes

This change supersedes only the root-Scope representation in:

- `refactor-canonical-git-remote-contract`
- `refactor-unified-authorization-boundaries`

Their security and transport invariants remain normative.

## Impact

- Affected spec: `repository-targets`
- Affected schema: `repository_scopes`, `access_surfaces`,
  `project_workspace_bindings`, credential/binding RPCs, RLS, and database tests
- Affected backend: Scope, Access Surface, Workspace Binding, authorization,
  Git admission, RuntimeGrant, readiness, repository-view state, and docs
- Affected clients: Web Project Access/Data surfaces and PuppyOne Desktop Cloud
- Release dependency: exact-SHA database-before-application gate from ISSUE-032
- Compatibility: older Desktop protocol versions receive an explicit upgrade
  response; the backend does not serve both old and new target shapes
