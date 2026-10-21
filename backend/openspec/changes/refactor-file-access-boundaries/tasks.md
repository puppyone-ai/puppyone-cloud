## Implementation

- [x] Relocate Product operations/tree algorithms and share the native read context.
- [x] Update consumers and remove old Product compatibility modules.
- [x] Split CLI AP-FS into five modules and preserve public route contracts.
- [x] Place credential persistence and Git/CLI resolution in Access while
  preserving separate MCP admission and credential types.
- [x] Share existing upload policy without broadening its enforcement.
- [x] Verify behavior, dependency direction, snapshot cleanup and credential isolation.
- [x] Run required regression checks and update architecture maps and limitations.

## Verification

Verified locally on 2026-10-19 against `1df9d000` plus the four working-tree
refactor batches. Changes are not committed, merged or deployed.

- Pre-change focused selection: 189 passed; five actual CLI HTTP tests were
  blocked at local loopback binding by the restricted execution environment.
- Post-change focused API, CLI, credential, tree, history and architecture
  selection: 203 passed. Existing immutable OpenAPI fixtures were not changed.
- Required suite: `pytest -q -m 'not e2e and not integration and not network'`.
  3,255 passed in the restricted environment. Six loopback Git tests, five Redis
  socket fixtures and one process-inspection test needed local permissions.
  Those exact 12 passed on rerun, alongside all five actual CLI HTTP cases:
  required-suite total 3,267 passed, plus five CLI cases. The 885 skipped and
  399 deselected cases remain outside required-suite acceptance.
- Final dependency and Git migration guards: 41 passed. Guards reject retired
  Product/HTTP/credential/policy imports, keep core read/write/storage independent
  of file_operations, and prevent API/CLI adapters from importing old entrypoints.
- Added tests exercise both SHA-1/SHA-256 shared reads, single-pin cleanup after
  failures, bulk reader setup and rejection of a foreign grant before snapshot
  admission. Added CLI/MCP tests reject wrong stored credential types even with
  a matching prefix and preserve independent revocation/expiry and MCP's 501.
- All 68 original CLI functions/classes have identical ASTs after import-path
  normalization. Tree algorithms, upload policy and credential repository remain
  byte-identical; Product operation behavior differs only by delegation of the
  existing read context. No new storage or provider I/O was introduced.
- OpenSpec strict validation and Git whitespace checks passed. Documentation
  validation retains only the two pre-existing Desktop ISSUE-115/ISSUE-117 broken
  links and the existing cross-repository source-link warnings.

These checks do not remeasure hosted latency or certify live S3/PG/provider
integration. Git protocol relocation, native Scope enablement and the remaining
legacy MCP filesystem ownership are separate work.
