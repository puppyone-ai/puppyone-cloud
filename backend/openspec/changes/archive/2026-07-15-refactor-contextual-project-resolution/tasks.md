## 1. Backend control plane

- [x] 1.1 Add a dedicated canonical Project context schema/model.
- [x] 1.2 Return authorized Project metadata, capabilities and exact Scope.
- [x] 1.3 Keep legacy resolution confirmation-gated and responses secret-free.
- [x] 1.4 Add service/router/security tests and update architecture docs.

## 2. Desktop resolution

- [x] 2.1 Normalize every PuppyOne fetch/push remote and detect conflicts.
- [x] 2.2 Add the canonical context API client and discriminated state controller.
- [x] 2.3 Reconcile binding and locator identity without silent side effects.
- [x] 2.4 Add stale request protection across workspace/account/host switches.

## 3. Desktop data and UX

- [x] 3.1 Make contextual data idle without an authorized Project ID.
- [x] 3.2 Keep global catalog loading behind an explicit global/home boundary.
- [x] 3.3 Route resolved context directly and render local-only/recovery without
  Organization Project rows, Use here, or clone commands.

## 4. Verification

- [x] 4.1 Add backend and Desktop unit/integration/architecture regressions.
- [x] 4.2 Run focused/full checks and prove no database or Version Engine change.
- [x] 4.3 Close ISSUE-037 with evidence after all checks pass.
- [x] 4.4 Preserve retryable authorization failures across backend, Electron
  IPC and Desktop recovery without false Project-not-found classification.
- [x] 4.5 Retry one safe binding-storage transport failure, map exhaustion to
  retryable 503, and never automatically replay mutations.

## Verification record

- Desktop: 191 test files / 1251 tests passed; lint and production build passed,
  including repository, Local Workspace, localization, shared-UI, Markdown,
  viewer, Document Session, Sidebar, Agent, provenance, Automation/Plugin,
  TypeScript, bundle and packaged-artifact gates.
- Backend focused: 62 authorization, workspace-binding service/router and
  canonical context tests passed, including retryable 503 response semantics.
- Backend full: 2151 passed and 65 skipped.
- Changed implementation paths contain no database/data migration, S3 write,
  Version Engine kernel, RepoFacade, CAS, transaction or content-path change.
- Both `refactor-contextual-project-resolution` and the intersecting
  `refactor-project-owned-repository-targets` change pass OpenSpec strict
  validation. Keep this change active until deployment, then archive it through
  the normal repository process.
