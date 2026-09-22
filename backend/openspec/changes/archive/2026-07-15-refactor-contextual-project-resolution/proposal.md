# Change: Refactor contextual Desktop Project resolution

## Why

Desktop currently treats an open Local workspace without a verified binding as
permission to browse the Organization Project catalog. Canonical Git locators
already identify one Project/Scope, but the control-plane endpoint still
returns a legacy confirmation candidate and Desktop does not consume it.

## What Changes

- Return an authorized, secret-free Project/Scope context from canonical remote
  resolution while keeping legacy access remotes confirmation-gated.
- Resolve an open Local workspace to exactly one Project, local-only, or a
  recovery state without enumerating Organization Projects.
- Separate transient Project context from durable WorkspaceBinding lifecycle.
- Detect conflicting fetch/push/remotes and fail closed.
- Keep global Project browsing as an explicit home/global capability.
- Preserve the existing RuntimeGrant and Version Engine content path.

## Impact

- Affected specs: workspace-binding / Desktop Cloud context
- Affected code: `backend/src/platform/workspace_binding`, PuppyOne Desktop
  Cloud workspace/data/router code, focused backend/Desktop tests
- Database: no schema or data migration
- Version Engine: no change

## Approval

The user explicitly approved the target architecture in
`puppyone desktop/docs/architecture/local-and-cloud-ux.md` and requested full
implementation under ISSUE-037 on 2026-07-14.
