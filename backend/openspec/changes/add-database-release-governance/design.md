## Context

Supabase owns ordered schema history through `supabase/migrations` and
`supabase_migrations.schema_migrations`. It does not orchestrate Python code or
pause `db push` between two migration files. PuppyOne already has a small
`public.migration_log` sentinel table and an ARQ/Python backend, but its July
credential backfills are hard-coded into two duplicated deployment workflows.

The repository is open source. GitHub Actions therefore cannot be the only
way to execute or inspect an upgrade.

## Goals / Non-Goals

- Goals: official Supabase schema history, portable data migration execution,
  immutable release artifacts, fail-closed contract retirement, repeatable
  upgrades, and one implementation shared by hosted and self-hosted installs.
- Non-goals: a general workflow platform, a second database, automatic rollback
  of destructive schema changes, or rewriting migrations already applied to a
  shared database.

## Decisions

### Two migration lanes

`supabase/migrations` remains the only schema history. Pure SQL data changes MAY
remain there only when they are bounded, transactional, and require no pause,
secret, external service, or application runtime.

All other transformations live in `supabase/data_migrations/<id>/`. Each
directory contains a versioned manifest, exactly one SQL or Python entrypoint,
and a fail-closed verification SQL file.

### Portable runner, thin CI adapters

The backend exposes one CLI for list, lint, plan, run, status, and verify.
GitHub Actions calls this CLI. A self-hosted user can call the same command from
Docker, a shell, GitLab, or Jenkins. Workflow YAML MUST NOT name a concrete
migration script.

The manifest selects only a constrained runner kind and a relative entrypoint;
it does not contain an arbitrary shell command. Production execution is limited
to a protected branch and protected GitHub Environment.

Python entrypoints run in isolated mode as self-contained single files. The
runner does not expose the repository through `PYTHONPATH`, and catalog policy
rejects imports from PuppyOne's mutable application package. This keeps runtime
behavior inside the reviewed artifact plus the locked third-party environment.

### Minimal durable receipt

`public.migration_log` remains the durable per-database receipt. One row means
the migration completed and verification passed. The JSON summary records the
artifact checksum, source SHA, runner version, and verification fact. Absence
means incomplete. Detailed running and failure logs stay in the CI system.

Reusing a migration ID with different content fails closed. Migration code is
immutable after release.

### Expand, data, cutover, contract

An online change is delivered in separate releases:

1. Expand schema and deploy code compatible with old and new facts.
2. Run and verify the data migration in Qubits, then Production.
3. Cut application reads/writes to the new fact.
4. Add the contract migration only after both environment receipts exist.

Contract SQL also checks the receipt and remaining legacy rows. A fresh install
with no legacy rows is allowed to reach the final schema without running an
irrelevant historical backfill.

If a released artifact contains a reviewed `contract.pending.sql`, the later
schema Contract must be an exact copy. The data checksum and row verification
authorize retirement only after repository policy also proves the destructive
SQL itself has not changed since review.

### Current migration debt

- The applied 2026-07-11 SQL files are immutable. Their two Python backfills
  become `legacy: true` manifests so a lagging Production database can finish
  through the generic runner.
- Pre-governance schema files already shared through Qubits are recorded in an
  immutable checksum baseline. They may promote unchanged; the exception cannot
  be extended to new or modified files.
- Qubits already passed the 0711 contract; recording or re-running a legacy
  job there is a verified no-op when the legacy columns/config no longer exist.
- The 2026-07-12 authorization expand migration is not in shared environments.
  Its `repo_user_permissions` copy becomes a data migration. The destructive
  retirement SQL is kept outside `supabase/migrations` until Qubits and
  Production receipts exist.

### Database and process safety

The runner validates prerequisite schema versions, requires a clean manifest,
uses PostgreSQL advisory locks, verifies the result, and writes the receipt only
after success. SQL data migrations run in one transaction. Python migrations
must be idempotent because application work and receipt publication cannot be
one database transaction.

Standalone verification uses a read-only PostgreSQL transaction with bounded
client/server timeouts. SQL entrypoint verification stays inside the write
transaction so a failed postcondition rolls back both data and receipt.

Schema and data workflows use environment-scoped secrets, least permissions,
one concurrency group per database, explicit timeouts, and non-cancelable
deployments. Pull-request jobs receive no shared-database credentials. A
Production data run first re-verifies the same artifact against Staging. Direct
remote SQL writes remain a break-glass procedure only.

The pull-request workflow runs for every PR and publishes a stable final check.
It skips expensive database jobs only after a read-only path diff says the PR
has no database release changes, avoiding branch-protection deadlocks caused by
workflow-level path filters.

## Risks / Trade-offs

- Python work can finish immediately before receipt publication fails. The job
  must be idempotent and verification makes the next run safe.
- The existing receipt table is in `public`. RLS restricts it to service role,
  and summaries contain no secrets. Moving it to a private schema is optional
  future hardening, not required for this rollout.
- A contract takes multiple releases. This is intentional: collapsing the
  phases recreates the failure this change is removing.

## Migration Plan

1. Add the manifest format, runner, policy tests, and reusable workflows.
2. Register the two July credential backfills as immutable legacy entries.
3. Deploy the authorization expand migration without its destructive contract.
4. Run the authorization data migration in Qubits and Production.
5. Promote the contract SQL in a later PR after both receipts are verified.
6. Remove the temporary legacy credential compatibility only after Production
   is confirmed past 20260711070000.

## Open Questions

- None. The user approved the two-lane, portable-runner architecture on
  2026-07-12.
