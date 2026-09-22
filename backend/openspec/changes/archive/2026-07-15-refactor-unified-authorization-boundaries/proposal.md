# Change: Refactor unified authorization boundaries

## Why

Organization membership, human Project authorization, local workspace identity,
and machine scope credentials are currently interpreted by multiple services.
That permits org membership or a runtime credential to stand in for a Project
grant and makes the same action produce different decisions across entry points.

## What Changes

- Add one canonical Project policy decision point with fixed Admin, Editor, and
  Viewer capabilities and deny-by-default semantics.
- Make `project_members` the only explicit human Project membership source;
  org-visible Projects grant an implicit Viewer baseline and org owners inherit
  Project Admin.
- Add explicit, revocable local workspace bindings that identify a Cloud
  Project without granting access.
- Separate human JWT grants from machine runtime grants and issue independent,
  hash-only credentials per workspace binding.
- Derive Claude readiness from an active root Git surface and an accepted root
  head; non-root scopes never satisfy this precondition.
- Retire `repo_user_permissions` through a manifest-driven data migration and
  separately promoted contract, and remove Desktop project/scope/key scanning
  resolver after a blocking migration preflight.

## Impact

- Affected specs: `project-authorization`, `workspace-binding`,
  `project-runtime-readiness`
- Affected code: `src/platform/authorization`, `src/platform/project`,
  `src/platform/workspace_binding`, Project-scoped routers, Version Engine
  admission, Desktop Cloud binding and navigation
- Affected schema: the nine-table authorization/binding/runtime model defined
  in `design.md`; `project_workspace_bindings` is new and
  `repo_user_permissions` retirement is staged and fail-closed
- Breaking API change: Project responses expose effective authorization and
  Desktop uses binding/readiness endpoints instead of heuristic discovery
