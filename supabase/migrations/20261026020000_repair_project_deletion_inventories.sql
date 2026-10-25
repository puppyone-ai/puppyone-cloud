-- Repair deletion jobs recorded before 20261007070000.
--
-- That release made the runtime deletion inventory native-only (historical
-- namespace bytes belong to the portable migration artifacts) but left
-- unfinished jobs with the former object_prefixes. Every claim updates the job
-- row, so project_deletion_jobs_prefixes_check rejected the whole claim batch
-- and no deletion job (including its sandbox resources) could progress.
-- Recompute exactly the value the constraint requires; finished jobs keep
-- their recorded history.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

UPDATE public.project_deletion_jobs
SET object_prefixes = public._project_deletion_object_prefixes(project_id, storage_principals)
WHERE status <> 'completed'
  AND object_prefixes IS DISTINCT FROM public._project_deletion_object_prefixes(project_id, storage_principals);

COMMIT;
