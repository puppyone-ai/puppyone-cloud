# Database baseline candidates

This directory contains **candidates**, not an additional migration input.
Supabase continues to execute only `supabase/migrations/*.sql`. No candidate
changes a hosted database, its migration history, or the existing release gates.

Generate and verify a candidate in a disposable local Supabase stack:

```bash
python3 scripts/database_baseline.py prepare --output /tmp/puppyone-baseline-b1
```

Verify a checked-in candidate against its checksum-pinned source migrations:

```bash
python3 scripts/database_baseline.py verify --candidate supabase/baselines/b1
```

The tool accepts no database URL, remote project, or linked target. It creates
its own temporary Supabase workdir and stops its containers on exit. Required
tools are Python 3.12+, Docker, and the CI-pinned Supabase CLI. No credentials,
PuppyPay checkout, production snapshot, or application environment are used.

The output includes a flattened `baseline.sql`, the source migration hashes,
and verification evidence. Generation preserves public schema ownership, ACLs,
RLS, functions, and indexes, plus the application trigger on `auth.users`.
Fresh-install reference data is explicitly reviewed in `required_data.sql`.
Any unexpected nonempty application table causes generation to fail.

## Activation is a separate release

Before moving a candidate into `supabase/migrations/<version>_baseline_b1.sql`
and retiring its predecessors, implement and verify ALL of these transitions:

1. Public, version-pinned upgrade path for databases older than the cutoff.
2. Schema/data verification before reconciling covered migration-history rows.
3. Baseline-aware data-job prerequisites (currently exact historical IDs).
4. Replacement of the old hash inventory and historical catch-up references,
   without bypassing immutable-history checks for unrelated changes.
5. Hosted release preflights, Docker bootstrap, and upgrade fixtures using the
   same supported baseline; staged rollout before production adoption.

These are activation blockers, not reasons to rewrite old migrations in place.
The preparation CI deliberately has read-only repository permissions and no
deployment secrets. It never activates a baseline automatically or monthly.

After activation, the executable baseline belongs only in `migrations/`;
retain this directory's manifest and evidence, and archive the old source in an
immutable public release. Do not maintain two editable copies of the SQL.
