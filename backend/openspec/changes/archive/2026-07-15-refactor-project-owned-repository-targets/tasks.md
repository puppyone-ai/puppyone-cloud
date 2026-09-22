## 1. Contract and inventory

- [x] 1.1 Freeze the target union, final relational model, error codes, and
  protocol version contract.
- [x] 1.2 Inventory every `repo_scopes`, `is_root`, `root_scope_id`,
  `binding_kind`, `_scope`, and root-path use and classify its final meaning.
- [x] 1.3 Add cross-repository JSON contract fixtures and architecture guards.

## 2. Database cutover

- [x] 2.1 Add read-only preflight checks for root, Scope, Surface, Binding,
  credential, tenant, and duplicate corruption.
- [x] 2.2 Rename the table, map root references to NULL, delete root rows, and
  install the final non-root Scope constraints.
- [x] 2.3 Remove `is_root` and `binding_kind`; update composite FKs, nullable-root
  uniqueness, indexes, RLS, and database client mappings.
- [x] 2.4 Replace binding/credential/resolve RPCs with the final target contract.
- [x] 2.5 Add fresh-install, upgrade, corruption-blocking, concurrency, and
  credential-continuity database tests.

## 3. Backend

- [x] 3.1 Add typed RepositoryTarget and ResolvedRepositoryView domain models.
- [x] 3.2 Make Scope CRUD manage only non-root scopes and remove root creation.
- [x] 3.3 Make Access Surfaces and Workspace Bindings target Project root or an
  exact Scope without persisted/public binding kind.
- [x] 3.4 Update canonical context resolution and Project-first authorization.
- [x] 3.5 Update single-snapshot machine credential resolution, RuntimeGrant,
  Git admission, RepoFacade, and scoped filesystem consumers.
- [x] 3.6 Remove root Scope identity from readiness and Project-wide hosting.
- [x] 3.7 Reclassify root view state as Project/view projection rather than
  Scope resource identity.

## 4. Web and Desktop

- [x] 4.1 Update Web types, Project Access/Data UI, Git URLs, Scope CRUD, and
  readiness to use the target union and true Scopes only.
- [x] 4.2 Update Desktop API types, binding resolver, remote matcher, explicit
  connect/repair/detach, readiness, and Cloud state to use the target union.
- [x] 4.3 Preserve local-only, missing remote, missing Scope, wrong account/host,
  revoked binding, retryable outage, and stale-generation UX.
- [x] 4.4 Prove local config remains secret-free and stores no root Scope ID.

## 5. Verification and release evidence

- [x] 5.1 Run backend unit/integration/Git/Version Engine suites and changed
  contract-module lint checks.
- [ ] 5.2 Run PostgreSQL migration reset/upgrade and invariant tests.
- [x] 5.3 Run Web typecheck/build and cross-client architecture guards.
- [x] 5.4 Run Desktop tests/typecheck/build and target contract tests.
- [x] 5.5 Update architecture/API/database/Desktop/release documentation.
- [x] 5.6 Record staging cutover/restore evidence or leave deployment-only
  acceptance explicitly NOT_VERIFIED.

`5.2` is intentionally open locally: the Docker/Supabase runtime was not
available. The required CI job runs fresh reset, previous-schema upgrade,
dirty-data rejection, concurrent enable, pgTAP, and postflight before merge;
staging/production remain blocked until their evidence records are attached.
