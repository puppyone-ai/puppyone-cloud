## 1. Authorization contract

- [x] 1.1 Add canonical Project roles, actions, capabilities, grants, policy,
  repository, dependencies, and route authorization manifest.
- [x] 1.2 Return effective role, grant source, and capabilities from Project
  list/detail while hiding inaccessible private Projects.
- [x] 1.3 Convert Project-scoped human routes and Version Engine admission to
  named action checks; keep machine credentials on RuntimeGrant only.

## 2. Database and migration

- [x] 2.1 Harden `project_members` tenant/audit constraints and make Project +
  creator Admin creation transactional.
- [x] 2.2 Add `project_workspace_bindings` and binding-scoped credential
  lifecycle constraints/RPCs.
- [x] 2.3 Add the blocking legacy permission data migration, remove all runtime
  use of `repo_user_permissions`, and stage its reviewed contract for promotion
  after Qubits and Production receipts.

## 3. Binding and readiness APIs

- [x] 3.1 Add create/get/heartbeat/delete binding endpoints that always
  re-authorize the current human.
- [x] 3.2 Add a constrained legacy-remote resolver that identifies a candidate
  but never authorizes or silently creates a full binding.
- [x] 3.3 Add root Git/head readiness endpoint and block Claude runtime loading
  until both facts are present.

## 4. Desktop

- [x] 4.1 Persist stable, secret-free binding identity and replace the N-by-M
  Project/scope/key scanner with binding API resolution.
- [x] 4.2 Render full/scoped/forbidden/revoked/wrong-account/wrong-host and Git
  prerequisite states without disabling local work.
- [x] 4.3 Drive Cloud navigation and mutations from server capabilities and
  gate Claude/Agent requests on readiness.

## 5. Verification and retirement

- [x] 5.1 Add policy matrix, identity separation, binding lifecycle, root
  readiness, route manifest, architecture guard, and migration contract tests.
- [x] 5.2 Run backend and Desktop unit/integration suites, lint, build, migration
  checks, and OpenSpec strict validation.
- [x] 5.3 Remove all legacy runtime imports/call sites and update architecture,
  API, migration, rollout, and rollback documentation.
