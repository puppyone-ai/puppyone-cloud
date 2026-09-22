# Change: Add portable database release governance

## Why

PuppyOne currently mixes Supabase schema migrations with application-language
data backfills. The staging and production workflows hard-code specific Python
scripts around `supabase db push`, so a green reset of an empty database does
not prove that an existing installation can upgrade safely. This also makes
the open-source upgrade path depend on PuppyOne's GitHub Actions setup.

## What Changes

- Keep schema and bounded transactional SQL changes in the official
  `supabase/migrations` history.
- Add immutable, manifest-driven data migrations under
  `supabase/data_migrations` for batched, resumable, secret-dependent, or
  application-language transformations.
- Add a portable backend CLI/runner. GitHub Actions, local installations, and
  other CI systems call the same runner rather than embedding migration logic.
- Reuse `public.migration_log` as the minimal per-database completion receipt;
  GitHub Actions remains the operational log.
- Split schema and data delivery workflows, enforce environment concurrency,
  and add repository policy and upgrade-path tests.
- Grandfather already-applied 2026-07-11 migrations without rewriting them;
  pin the complete pre-governance Qubits schema history by checksum and package
  the remaining production backfills as legacy data migrations.
- Split the not-yet-shared `repo_user_permissions` retirement into a data
  migration followed by a separately promoted contract migration.

## Impact

- Affected specs: `database-release-governance` (new)
- Affected code: `supabase/`, backend infrastructure CLI, database tests, and
  GitHub database workflows
- Deployment: Qubits runs every data migration before Production. A contract
  migration is promoted only after both databases have a matching completion
  receipt or the legacy relation is provably empty.
