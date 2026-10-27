## Implementation
- [x] Add `object_recovery` validation (GitHub configuration and hosted coordinator).
- [x] Snapshot-mode recovery preflight without versioning; Supabase privacy fallback.
- [x] Verified object snapshot and restore module; snapshot during the backup phase.
- [x] Regression tests with an in-memory S3 (pagination, active writer, damaged copy, damaged restore, unchanged versioning mode).
- [x] Update database release governance documentation.

## Release acceptance (separate from implementation)
- Configure `object_recovery` and a private backup bucket in the production release configuration.
- Prove the snapshot/restore path against real Supabase Storage in the production rehearsal before the maintenance window.
