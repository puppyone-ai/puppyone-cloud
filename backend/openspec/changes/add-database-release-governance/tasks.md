## 1. Migration format and runner

- [x] 1.1 Add the `supabase/data_migrations` contract, schema, and contributor documentation.
- [x] 1.2 Implement the portable list/lint/plan/run/status/verify CLI with checksum and prerequisite enforcement.
- [x] 1.3 Add advisory locking, transactional SQL execution, Python execution, verification, and completion receipts.

## 2. Current migration state

- [x] 2.1 Register the July scope and surface credential backfills as immutable legacy data migrations.
- [x] 2.2 Split the unpublished authorization permission copy from its contract retirement.
- [x] 2.3 Update the authorization rollout specification and tests to represent the staged contract honestly.

## 3. CI/CD

- [x] 3.1 Replace script-specific staging and production schema workflows with one reusable schema workflow.
- [x] 3.2 Add protected, manually dispatched Qubits/Production data migration workflows that call the portable runner.
- [x] 3.3 Expand PR checks for manifest validity, migration immutability, destructive-contract policy, and runner tests.
- [x] 3.4 Publish one stable required PR check and require promoted Contract SQL to match its reviewed pending artifact exactly.

## 4. Verification and documentation

- [x] 4.1 Add unit tests for manifests, checksums, prerequisites, idempotence, locking, and failure behavior.
- [x] 4.2 Add database fixtures/contracts for fresh install and legacy upgrade behavior.
- [x] 4.3 Run targeted backend, workflow-contract, lint, formatting, and OpenSpec-shape validation.
- [x] 4.4 Require fresh-schema reset, pgTAP, and legacy-upgrade execution in database PR CI.
- [x] 4.5 Document operator commands, environment secrets, rollout order, break-glass rules, and commit conventions.
