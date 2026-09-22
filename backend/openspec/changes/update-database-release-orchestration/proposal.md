# Change: Automatically orchestrate protected database release lanes

## Why

The repository already has independent reusable schema and data-migration
workflows, but the Qubits orchestrator only runs the data lane after a manual
dispatch. A merge can therefore deploy compatible schema while leaving its
declared staged data migration incomplete. This contradicts the protected
release model: application deployment must be gated on the complete database
release for the same source SHA.

## What Changes

- Keep `_schema-deploy.yml` and `_data-migration.yml` as separate reusable
  CI/CD lanes.
- Make the Qubits release orchestrator automatically execute the staged
  release pointer on every protected `qubits` push in this order: schema,
  optional repair, data plan, data run, data verify, final schema check.
- Make the Production release orchestrator follow the same automatic pattern
  on protected `main` pushes, while requiring verified Qubits evidence before
  a Production data write.
- Retain the standalone `Data Migration` workflow only for read-only
  diagnosis and controlled recovery; it is not a required release step.

## Impact

- Affected spec: `database-release-governance`
- Affected code: GitHub database workflows, release pointers, release docs and
  workflow-contract tests
- Deployment: a protected branch push can fail before application autodeploy
  when its staged data artifact has missing prerequisites, secrets, or failed
  verification.
