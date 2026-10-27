## Second batch

- [x] Move the existing Web repository HTTP modules into the Access API adapter.
- [x] Relocate shared schemas outside the Web adapter and update all callers.
- [x] Preserve route mounting and add dependency boundary regression checks.
- [x] Verify public contracts, authorization, reads/writes and the required
  non-network regression suite.
- [x] Update the owning architecture maps and record verification limits.

## Verification

Verified locally on 2026-10-19 against `1df9d000` plus the first and second
refactor batches. Completion means local implementation, not merge or deployment.

- The same focused contract, authorization, file operation, resource, history
  and repository management selection passed before and after: 139 passed.
  Eleven opt-in PG/PostgREST/S3 integration cases were skipped in each run;
  this batch does not claim a new live I/O or latency measurement.
- Required suite: `pytest -q -m 'not e2e and not integration and not network'`.
  3,243 passed in the restricted environment. Six loopback HTTP tests, five
  Redis socket fixtures and one process-inspection test needed local socket
  and process permissions; those exact 12 cases passed on rerun.
  Combined: 3,255 passed; 885 skipped and 399 deselected remain outside acceptance.
- Existing immutable OpenAPI contract fixtures passed without modification.
  Authorization manifest coverage passed. Dependency guards cover retired
  module paths and prohibit Version Engine/file operations importing the Web
  adapter; a separate check covers the unchanged WebSocket mount.
- All thirteen moved modules have identical ASTs after normalizing module
  descriptions and import locations. Audit, download token, WebSocket and
  repository management implementations are byte-identical. Application
  assembly, AP-FS and Git metadata handlers differ only in imports.
- OpenSpec strict validation and staged/unstaged Git whitespace checks passed.
- The documentation validator still reports the two pre-existing broken
  ISSUE-115/ISSUE-117 links in Desktop `experimental-path-forks.md`, plus the
  existing cross-repository link warnings. The changed architecture documents
  introduce no validation errors.

The third batch (Product adapter/tree boundary and command compatibility
cleanup), CLI/Git entrypoint relocation, runtime/provider behavior, database
migrations and hosted deployment are outside this batch.
