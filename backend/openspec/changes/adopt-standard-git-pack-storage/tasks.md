# Tasks

- [x] Record the user-approved first slice and authority/resource boundaries.
- [x] Replace custom incoming unpack/delta code with owned stock Git indexing.
- [x] Persist standard packs with bounded S3 multipart and existing publication fences.
- [x] Preserve legacy reads and add bounded cold pack reads with fresh proof isolation.
- [x] Avoid repeating the physical pack upload for non-atomic multi-ref push.
- [x] Add compatible location SQL and prove populated upgrade/rollback/ACL/fence behavior.
- [x] Cover SHA-1/SHA-256, ordinary/thin/forward delta, corruption, empty packs and retry.
- [x] Cover 576 MiB logical bodies without a raw spool, cold reads and child cleanup.
- [x] Cover transfer failure and repeated cancellation before publication evidence.
- [x] Complete final broad regression and document exact results.
- [x] Add GitHub-hosted PR coverage for real S3/PostgREST/Linux and populated SQL upgrade.
- [x] Unify configurable receive admission and separate completed/cold-read pack budgets.
- [x] Test real packs above 128 MiB, policy reduction, metadata allowance and thin completion.
- [x] Enforce the user-approved 2 GiB single-push ceiling and explicit Desktop size rejection, without automatic splitting.
- [x] Verify first/initial-attempt/incremental pushes share admission and oversized pushes preserve refs, objects and local work through real Git/HTTP/PG.
- [ ] Run owned real S3/PostgREST cold-clone and Product compatibility acceptance (local Docker cannot start a container).
- [ ] Complete hosted activation and representative <5-second benchmark (outside this implementation request).
