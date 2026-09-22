## 1. Architecture and contracts

- [x] 1.1 Approve the canonical locator, credential, RuntimeGrant, binding, and
  migration design.
- [x] 1.2 Promote the contract into architecture documents 05, 12, 01, 06, and
  CLI/user guidance with one normative owner and no duplicated authority.
- [x] 1.3 Register root and scoped Git routes in the authorization manifest.

## 2. Credential and database boundary

- [x] 2.1 Add credential-level `grant_mode` and explicit
  `credential_lifecycle` revocation domains with database constraints and a
  deterministic effective-mode calculation.
- [x] 2.2 Issue new Git credentials as hash-only `git_http_token` records bound
  to active `git_remote` Access Surfaces.
- [x] 2.3 Add one transactional/repository resolution path that validates
  credential, Project, Scope, Surface, Binding, mode, expiry, and current human
  capability where applicable.
- [x] 2.4 Preserve independent shared `r`, shared `rw`, short-lived session,
  and per-binding rotation/revocation semantics.

## 3. Git protocol routes

- [x] 3.1 Add `/git/{project_id}/scopes/{scope_id}.git` smart-HTTP, health, and
  rebuild-cache routes.
- [x] 3.2 Make `/git/{project_id}.git` strictly root-only.
- [x] 3.3 Extract Basic/Bearer credentials from headers, return a standards-
  compliant Basic challenge, and redact every secret-bearing surface.
- [x] 3.4 Refactor duplicate root/AP handlers behind one resolved Git target and
  shared upload-pack/receive-pack implementation.
- [x] 3.5 Keep the old `/git/ap/{access_key}.git` route as an instrumented,
  bounded compatibility adapter without redirects.
- [x] 3.6 Keep Git health/rebuild on the machine RuntimeGrant plane and expose
  root-only human health/rebuild through separately authorized Project
  control-plane adapters.

## 4. Binding and issuance APIs

- [x] 4.1 Return canonical remote metadata and the one-time credential as
  separate fields from Access and Workspace Binding issuance/rotation.
- [x] 4.2 Add deterministic canonical-locator resolution for Desktop without
  Project/scope/key scanning or implicit human authorization.
- [x] 4.3 Make binding creation and failure compensation atomic or reliably
  revocable when local remote/credential configuration fails.

## 5. First-party clients

- [x] 5.1 Configure Desktop root/scoped remotes with canonical URLs and an
  OS-backed, path-aware Git credential helper.
- [x] 5.2 Open an already-bound canonical Project directly after login and render
  scoped workspaces as scoped, never as full Projects.
- [x] 5.3 Update Web access/connect surfaces, CLI instructions, Sandbox
  provisioning, and internal jobs to stop constructing key-bearing URLs.
- [x] 5.4 Implement verified conversion of legacy remotes before removing the
  secret-bearing local URL.
- [x] 5.5 Convert Web CLI setup surfaces to explicit one-time Scope credential
  issuance; keep Scope/Repo Identity/dashboard discovery redacted and never
  turn a masked hint or placeholder into a runnable command.

## 6. Verification and rollout

- [x] 6.1 Add route/auth unit tests for exact root/scoped target matching,
  lifecycle, mode, binding, and disclosure behavior.
- [x] 6.2 Add real Git CLI clone/fetch/push tests for canonical root and scoped
  locators using standard credential helpers.
- [x] 6.3 Prove old/new routes produce equivalent RepoFacade, cache, CAS, audit,
  and readiness facts during migration.
- [x] 6.4 Add Desktop tests for direct bound navigation, candidate discovery,
  wrong host/account/scope, credential storage, and failure compensation.
- [x] 6.5 Add redacted legacy-usage telemetry, release gates, rollback checks,
  and a separate approval gate for final legacy route removal.
- [x] 6.6 Run backend, database, Desktop, Web, CLI, Sandbox, architecture, and
  OpenSpec strict validation suites.

## Verification record — 2026-07-13

- Canonical/Version Engine suite: 105 passed, including stock Git root/scoped
  clone, fetch, push, credential-helper, legacy equivalence, CAS, and readiness.
- Focused credential/binding/security suite: 58 passed after lifecycle-domain
  hardening; the final canonical auth subset passed 44 tests.
- Full backend non-external suite: 1,935 passed, 15 skipped, 50 deselected. Four
  unrelated baseline tests remain red (Agent missing-prompt auth expectation,
  two undefined test fixtures in content-read tests, and an object-GC smoke
  wrapper assertion); none touches this change's paths.
- Database: the real migration executed transactionally in an isolated
  PostgreSQL database and passed behavioral assertions for backfill, exact
  mode narrowing, shared r/rw/session isolation, binding issue, and failure
  compensation. A reusable 18-assertion pgTAP test is checked in.
- Desktop: 169 files / 1,075 tests passed; boundary and TypeScript checks
  passed. Web TypeScript and production build passed. CLI unit tests passed.
- Python compilation, changed-source fatal lint checks, both repositories'
  `git diff --check`, static legacy-secret scans, and OpenSpec strict validation
  passed.

## Final continuation audit — 2026-07-14

- Re-audited every Web Git setup surface. The empty-workspace dialog now keeps
  an explicit route to create or manage the separately issued Git credential;
  it never treats the locator as a credential.
- Traced the reported Desktop `Use here` 500 to a long-lived backend process
  whose loaded Scope row mapper still required the retired plaintext
  `repo_scopes.access_key` column. The current mapper is column-independent;
  a functional regression test now locks that behavior, and the rollout
  requires a full worker recycle plus a Scope-list smoke check.
- Removed Desktop's redundant Repo Identity/Scope preflight from `Use here`
  and backup attachment. A selected Project ID now goes directly to Workspace
  Binding creation, whose response owns the canonical URL and credential.
- Closed the reused-binding compensation gap: if local remote/helper setup
  fails after credential rotation, Desktop revokes that credential; if the
  binding itself was newly created, Desktop revokes the binding instead.
- Tightened canonical parsing so percent-encoded Project/Scope identity cannot
  create an alternate spelling of the same locator.
- Corrected the Web Git-health boundary: JWT-backed diagnostics now use a
  Project Read control-plane endpoint, cache repair requires Project Manage,
  and `/git/...` remains machine-credential-only. Added service, route,
  manifest, capability-UI, and cross-plane regression tests.
- Fixed the adjacent CLI reveal path after plaintext Scope-column removal:
  regenerate now returns the transient key once, metadata/list/dashboard reads
  stay redacted, and active Web setup surfaces require explicit issuance before
  presenting copyable commands.
- Web TypeScript and a clean production build passed after removing stale
  generated `.next` state. Both repositories still pass `git diff --check`,
  and the OpenSpec change still passes strict validation.
- A final socket-enabled backend run passed 1,954 tests with 15 skipped and 50
  deselected. The only 3 failures are pre-existing test defects outside this
  change: two undefined `_FakeProjectService` fixtures and one stale GC smoke
  wrapper text assertion. Canonical Git/binding/auth focused tests passed 56/56.
- The Desktop session-restore path is now catalog-gated: a Local binding or
  canonical target resolves by exact ID without racing an Organization Project
  listing; explicit unbound browsing and Cloud-only browsing still load the
  catalog. The final Desktop focused set passed 46/46 and TypeScript passed.
- Web one-time Git issuance now rejects a credential response unless its
  canonical locator, requested mode, fixed username, and token format all match
  the selected Access Surface. Web TypeScript and production build passed again.
