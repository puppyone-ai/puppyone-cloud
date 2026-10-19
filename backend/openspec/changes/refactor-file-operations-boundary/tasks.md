## First batch

- [x] Move the two command implementations into `src/file_operations` and retain
  compatibility exports with one shared implementation.
- [x] Update Web write and assembly imports without moving HTTP routes or Git
  transaction code.
- [x] Verify old/new import compatibility and existing operation behavior; run
  the repository-required non-network regression suite.
- [x] Update the owning architecture documents and record verification limits.

## Verification

Verified locally on 2026-10-19 against `1df9d000` plus this working-tree change.
Completion here means local implementation and checks, not merge or deployment.

- Pre-change focused regression: 96 passed.
- Post-change command, Web write, AP-FS and native command regression: 97 passed,
  including identity checks for old/new exports and Web/container assembly.
- Required suite: `pytest -q -m 'not e2e and not integration and not network'`.
  3,241 passed in the restricted execution environment; six loopback HTTP tests,
  five Redis socket fixtures and one process-inspection test could not run there.
  Those exact 12 cases passed when rerun with local socket/process permissions.
  Combined: 3,253 passed; 885 skipped and 399 deselected remain outside acceptance.
- Both moved implementations have the same AST as the originals after ignoring
  the module description and compiler import location. No added runtime I/O.
- Ten source/test paths affected; specification and architecture docs are separate.
- OpenSpec strict validation and Git diff whitespace checks passed.
- The Puppyone issues documentation validator still reports two pre-existing
  broken ISSUE-115/ISSUE-117 links in Desktop `experimental-path-forks.md`.
  No errors were reported for the modified architecture documents.
