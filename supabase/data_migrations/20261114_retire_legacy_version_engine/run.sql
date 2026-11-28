-- Nothing is transformed. 20261007_repository_recovery_archive already copied
-- every pre-native record into verified, immutable per-project manifests. The
-- runner executes verify.sql in this transaction after writers are drained,
-- and its receipt is what authorizes the Contract to drop the old tables.
SELECT 1;
