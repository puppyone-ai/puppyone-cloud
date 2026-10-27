# Change: Snapshot object recovery for storage that cannot be versioned

## Why

A breaking release proves bucket versioning on both the object and backup
buckets before it stops anything, and requires an object version for the
uploaded database backup. Supabase Storage (used by staging and production)
cannot enable versioning and answers the S3 policy-status call without
`IsPublic`, so every coordinated (quiescing) release fails its recovery
preflight. The native repository migration therefore cannot run through the
protected release program on either environment.

## What Changes

- Add an optional `object_recovery` release policy. Absent means the current
  bucket-versioning behaviour, unchanged.
- `{"mode": "snapshot", "approved_by", "reason"}` replaces the versioning
  requirement with a verified copy of every object into the separate private
  backup bucket after writers stop, plus a digest-bound manifest and a tested
  restore that never writes a damaged copy.
- Backup-bucket privacy falls back to the Supabase Storage `public` flag when
  the S3 policy status is inconclusive.
- The database backup in snapshot mode is checksum-verified without an object
  version and without the SSE request header.

## Approval and authority

The user approved this approach ("option B") on 2026-10-27 after the production
inventory showed Supabase Storage cannot be versioned. Canonical description:
`docs/architecture/13-database-release-governance.md` (Data scope and recovery).

## Impact

- Affected code: `backend/src/infra/data_migrations/release/hosted.py`, new
  `release/object_snapshot.py`, `supabase/releases/github_actions.py`.
- No schema, data artifact or workflow change; staging/production behaviour is
  unchanged until a release configuration opts into snapshot mode.
