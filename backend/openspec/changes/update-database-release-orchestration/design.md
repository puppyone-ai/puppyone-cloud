## Context

Schema and data mutations already use independent reusable workflows with the
same environment-scoped concurrency group. `migrate-staging.yml` currently
uses the complete sequence only for `workflow_dispatch`; a normal `qubits`
push invokes only schema deployment. Production currently invokes only schema
deployment on `main`.

## Goals / Non-Goals

- Goals: make each protected-branch release complete automatically, retain
  immutable artifacts and receipts, and preserve Qubits-before-Production
  evidence.
- Non-goals: remove the portable runner, perform laptop-to-shared-database
  writes, bypass protected environments, or automatically roll back data.

## Decisions

1. The existing reusable workflows remain the schema and data CI/CD lanes.
   The environment-specific `migrate-staging.yml` and
   `migrate-production.yml` workflows become orchestrators only.
2. Every protected push resolves a versioned release pointer. A completed
   artifact is idempotently planned, run, and verified, so code-only merges do
   not require a special path.
3. Production data writes first run a read-only verify of the exact artifact
   against Qubits. A failed or absent Qubits receipt prevents Production.
4. The standalone data workflow remains available for plan/verify and
   recovery, but normal releases do not require a human dispatch.

## Risks / Trade-offs

- A data migration can delay application deployment. This is intentional:
  Railway's Wait for CI protects the same-SHA database contract.
- A stale release pointer will be retried on later pushes. Artifact receipts
  make this a no-op after success; a failure remains visible and blocks safely.

## Migration Plan

1. Update the release orchestration workflows and release pointers.
2. Add workflow-contract tests for automatic ordering and Production evidence.
3. Merge to Qubits and verify schema/data/final-schema attestation on one SHA.
4. Promote to main and verify the analogous Production sequence.
