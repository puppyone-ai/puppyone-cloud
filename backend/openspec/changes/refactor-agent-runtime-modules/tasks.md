## Implementation
- [x] Record the accepted module layout in the canonical architecture document.
- [x] Move orchestration and update source/test consumers without changing wire or database names.
- [x] Inject Loop, Execution and Files capabilities and preserve current sandbox behavior.
- [x] Verify replacement boundaries, existing recovery/approval/publication behavior and artifact packaging.
- [x] Run repository checks and update implementation provenance and documentation links.

## Validation
Backend regression: 3,184 passed; 885 opt-in cases skipped. Native PostgreSQL /
PostgREST contracts: 92 passed. Real pinned Pi harness: 6 passed, including
independently injected filesystem and execution tools. Node tool/controller/Git
contracts: 20 passed. OpenSpec strict validation and source boundary checks pass.
The Docker artifact builds; full Docker runtime integration could not complete
because this machine's new containers remained in Created state. No hosted
provider or paid model acceptance is claimed.
