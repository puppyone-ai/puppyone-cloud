## Implementation

- [x] Relocate Git routes and transport, preserving core and Agent boundaries.
- [x] Relocate MCP dispatch/resolution and existing shared filesystem helpers.
- [x] Relocate Scope CRUD/identity and separate credential resolution from geometry.
- [x] Update callers, remove retired paths and enforce dependency direction.
- [x] Run protocol, authorization, resource-cleanup and required regression checks.
- [x] Update canonical architecture, verification evidence and acceptance limits.

## Verification

Verified locally on 2026-10-19 against `1df9d000` plus the six working-tree
refactor batches. The checks below preceded the user-requested source commit
and local qubits integration; they do not establish deployment acceptance.

- Pre-change Git/MCP/Scope/credential/architecture selection: 131 passed.
- Required suite: `pytest -q -m 'not e2e and not integration and not network'`.
  Restricted execution produced 3,264 passed, 885 skipped and 399 deselected.
  Six stock-Git HTTP cases, five Redis queue cases and one process-reaping case
  were blocked by localhost/process permissions. Those exact 12 passed with
  local permissions, alongside all five actual CLI HTTP cases: required-suite
  total 3,276 passed, plus five CLI cases. No hosted system was mutated.
- Final architecture, Git migration, credential and MCP gates: 72 passed.
  Added cases verify Scope Project binding/read-only clamping without changing
  stored geometry, and both internal MCP routes reject missing configuration
  or wrong internal secrets before attempting bearer resolution.
- All 160 top-level functions/classes retained in their moved modules have
  identical ASTs after import/docstring normalization. Scope CRUD classes,
  schemas and dependency construction were split into existing owners; only
  unused legacy permission DTOs were removed. Git framing, pack algorithms,
  stream limits and cleanup, Agent relay and MCP tool behavior are preserved.
- Runtime sources contain no imports of the retired repo, Git entrypoints,
  Git adapter, scoped_fs or internal MCP runtime modules. Dependency guards
  prevent Version Engine from importing Access adapters/shared filesystem,
  shared filesystem from importing protocol adapters, and Scope models,
  repository and service from importing Access. The Scope deletion router
  retains its pre-existing Access reference check as cross-domain orchestration.
- Existing OpenAPI/contract fixtures are unchanged. No new storage/provider
  round trips were introduced; existing request-budget tests ran in the suite.
- OpenSpec strict validation and Git whitespace checks passed. Documentation
  validation retains only the pre-existing Desktop ISSUE-115/ISSUE-117 broken
  links and 25 existing unresolved cross-repository source-link warnings.

## Overall acceptance boundary

The six structure batches are implemented. The subsequent explicit acceptance
has **61 passed, one failed**; see [the receipt](acceptance.json).

- Owned PostgreSQL/PostgREST/MinIO entry, ownership, leases, retention and native
  Agent publication (including SHA-1/SHA-256) checks: 53 passed.
- Web save/rename -> real stock-Git fetch/push -> Web visibility, stale-write
  conflict, delete and credential revocation: passed. Production JWT validation,
  routes, grants and native storage are used; fixture identities/repositories
  and locally signed JWTs do not prove Supabase login or hosted deployment.
- The continuous Web/Git/CLI case fails at actual `puppyone fs cat` with 403:
  the Project-root credential resolver reaches `admit_cli_fs_command`, which
  requires a Scope. That policy file is unchanged from `1df9d000`; native Scope
  remains unavailable. The failing non-default acceptance case is retained,
  without xfail or replacing its assertion with an expected rejection.
- Seven owned migration gate/rollback/reapply checks passed. Full populated
  Docker/Supabase upgrade remains unverified: a newly owned busybox container
  stayed Created and was removed; existing user containers were not touched.
- Fresh Fly runtime/I/O tests did not start: pushing the unchanged worker image
  to a disposable app timed out at 300 seconds, then at 180 seconds using
  isolated registry credentials. No new runtime latency is claimed. Resource
  cleanup is recorded in the receipt. Earlier successful Fly results retain
  their original source identities in the workbench acceptance directory.
- E2B inventory contains only older worker templates. Uploading the current
  template still needs the explicitly requested authorization after the earlier
  automatic-review rejection. Same-workload E2B I/O remains unverified.

Reproduce with the owned-service environment described in
`tests/agent/workbench_acceptance/README.md`:

```sh
python -m pytest -q tests/repository_hosting/accept_access.py \
  tests/agent/runtime/test_migration_release.py
```

The user requested committing and merging the candidate into local qubits
before these outstanding acceptance items are closed. This is source
integration, not a validated release or hosted activation. Native Scope/MCP
enablement, full workspace product-chain acceptance and Table/share retirement
remain separate work.
