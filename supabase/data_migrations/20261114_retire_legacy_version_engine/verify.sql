DO $$
DECLARE
  legacy text;
  remaining bigint;
BEGIN
  -- Read-only and rerunnable before or after the Contract: dropped tables and
  -- columns are skipped, so operators can run it as a preflight on any target.
  FOREACH legacy IN ARRAY ARRAY[
    'version_commits', 'version_scope_state', 'version_view_commits', 'version_outbox',
    'version_conflicts', 'version_transactions', 'version_refs', 'local_shadow_snapshots'
  ] LOOP
    IF to_regclass('public.' || legacy) IS NOT NULL THEN
      EXECUTE format(
        'SELECT count(*) FROM public.%I legacy_row
         WHERE NOT EXISTS (
           SELECT 1 FROM public.version_repository_archives archive
           WHERE archive.project_id = legacy_row.project_id
             AND archive.state = ''verified'' AND archive.manifest_sha256 IS NOT NULL
             AND archive.verified_at IS NOT NULL AND archive.source_deleted_at IS NOT NULL
         )', legacy) INTO remaining;
      IF remaining > 0 THEN
        RAISE EXCEPTION 'LEGACY_HISTORY_NOT_ARCHIVED:%', legacy;
      END IF;
    END IF;
  END LOOP;
  -- The mut_* columns are dual-written copies. Only a Project still outside the
  -- native engine depends on its legacy root, so prove the copy is exact there.
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
             AND table_name = 'projects' AND column_name = 'mut_root_hash') THEN
    EXECUTE 'SELECT count(*) FROM public.projects project
             WHERE project.version_root_hash IS DISTINCT FROM project.mut_root_hash
               AND NOT EXISTS (SELECT 1 FROM public.version_repositories repository
                               WHERE repository.project_id = project.id
                                 AND repository.authority = ''native'')' INTO remaining;
    IF remaining > 0 THEN
      RAISE EXCEPTION 'LEGACY_ROOT_COPY_MISMATCH';
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
             AND table_name = 'synchronize_github_logs' AND column_name = 'mut_commit_id') THEN
    EXECUTE 'SELECT count(*) FROM public.synchronize_github_logs
             WHERE version_commit_id IS DISTINCT FROM mut_commit_id' INTO remaining;
    IF remaining > 0 THEN
      RAISE EXCEPTION 'LEGACY_COMMIT_COPY_MISMATCH';
    END IF;
  END IF;
END $$;
