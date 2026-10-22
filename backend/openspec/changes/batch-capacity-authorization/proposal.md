# Batch capacity authorization

## Why

A 6,017-object push executes 24,068 credential resolutions and 48,136 role
lookups inside row triggers even though metadata already uses one RPC. On the
tested hosted database the reservation exceeds its eight-second statement
timeout before S3 upload starts.

## Approval and scope

The user approved implementation on 2026-10-23: authorize once per bounded
capacity transaction, preserve current permissions/expiry/concurrency, converge
old and page entry points, and update architecture and regression evidence.
This approval covers local code and disposable-database validation; hosted
migration/deployment evidence is recorded separately.

## Changes

- One explicit capacity admission boundary inside the existing page RPC.
- A shared current-actor resolver does one canonical role resolution.
- Transaction-local locked facts and expiry checks replace per-object authorization.
- The 200-object compatibility RPC delegates to the same implementation.
- Preserve owner-only migration/archive writers, SQL constraints, quota accounting,
  publication fencing, and the final ref transaction.
- Replace the remaining per-row bare-object size guard with a set-based statement guard.
- Add populated upgrade/rollback, authorization, atomicity, concurrency and call-count tests.

No new service, Redis dependency, durable authorization cache, table or pack
format is introduced. S3 transfer and final ref publication retain their current
boundaries. Route round-trip consolidation and uncertain-result classification
are separate follow-up work.

## Release authorization

On 2026-10-23 the user authorized database migrations through the `qubits`
branch CI/CD after local tests pass. Backend-only changes stay local. This
release includes only this compatible SQL migration and its tests/documentation.
