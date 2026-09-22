## 1. Workflow implementation

- [x] 1.1 Make the Qubits orchestrator resolve and execute its staged data release on every protected push.
- [x] 1.2 Make the Production orchestrator resolve and execute its promoted data release on every protected push after Qubits evidence.
- [x] 1.3 Preserve standalone read-only/recovery data-migration operations without making them a normal release prerequisite.

## 2. Verification and documentation

- [x] 2.1 Add workflow-contract tests for release ordering, idempotent completed artifacts, and Production evidence.
- [x] 2.2 Update database release governance documentation and release pointers.
- [x] 2.3 Run workflow/data-migration validation and OpenSpec validation.
