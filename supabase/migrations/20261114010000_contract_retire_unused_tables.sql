-- Retire tables with no runtime reader or writer: analytics logs, legacy
-- connectors/sync/Scope-sync state, Search Tool tasks and never-used
-- prototypes. Promote only together with its verified data artifact.
-- requires-data-migration: 20261114_retire_unused_tables
-- data-migration-checksum: f996abb6ec1a3ccd4f9dea8fa5a3d34a26120745b018b8d76adef246f3d2fb8f
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
LOCK TABLE public.project_storage_principals, public.api_keys, public.sandbox_endpoints,
  public.agent_profiles, public.bookmarks, public.subscriptions, public.connectors, public.chunks,
  public.sync_changelog, public.sync_state, public.scope_sync_settings, public.scope_sync_events,
  public.agent_logs, public.search_index_tasks, public.connector_runs, public.access_logs,
  public.agent_execution_logs IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
  -- Retired rows leave only through the coordinated release (drain + backup).
  IF (EXISTS (SELECT 1 FROM public.api_keys) OR EXISTS (SELECT 1 FROM public.sandbox_endpoints)
      OR EXISTS (SELECT 1 FROM public.agent_profiles) OR EXISTS (SELECT 1 FROM public.bookmarks)
      OR EXISTS (SELECT 1 FROM public.subscriptions) OR EXISTS (SELECT 1 FROM public.connectors)
      OR EXISTS (SELECT 1 FROM public.chunks) OR EXISTS (SELECT 1 FROM public.sync_changelog)
      OR EXISTS (SELECT 1 FROM public.sync_state) OR EXISTS (SELECT 1 FROM public.scope_sync_settings)
      OR EXISTS (SELECT 1 FROM public.scope_sync_events) OR EXISTS (SELECT 1 FROM public.agent_logs)
      OR EXISTS (SELECT 1 FROM public.search_index_tasks) OR EXISTS (SELECT 1 FROM public.connector_runs)
      OR EXISTS (SELECT 1 FROM public.access_logs) OR EXISTS (SELECT 1 FROM public.agent_execution_logs))
     AND NOT EXISTS (
       SELECT 1 FROM public.migration_log WHERE name = '20261114_retire_unused_tables'
       AND coalesce((summary->>'verified')::boolean, false)
       AND summary->>'artifact_checksum' = 'f996abb6ec1a3ccd4f9dea8fa5a3d34a26120745b018b8d76adef246f3d2fb8f'
     ) THEN
    RAISE EXCEPTION 'DATA_MIGRATION_REQUIRED:20261114_retire_unused_tables';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.search_index_tasks task
    WHERE task.project_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
      AND coalesce(task.created_by::text, task.project_id) ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
      AND NOT EXISTS (
        SELECT 1 FROM public.project_storage_principals stored
        WHERE stored.project_id = task.project_id
          AND stored.principal = coalesce(task.created_by::text, task.project_id)
      )
  ) THEN
    RAISE EXCEPTION 'RETIRED_SEARCH_TASK_OWNER_NOT_PRESERVED';
  END IF;
END $$;

-- Search task owners now live in project_storage_principals.
CREATE OR REPLACE FUNCTION public._project_deletion_storage_principals(p_project_id text, p_requested_by uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO 'pg_catalog', 'public', 'pg_temp' AS $function$
    SELECT COALESCE(jsonb_agg(source.principal ORDER BY source.principal), '[]'::jsonb)
    FROM (
        SELECT DISTINCT candidate.principal
        FROM (
            SELECT p_requested_by::text AS principal
            UNION ALL
            SELECT project.created_by::text
            FROM public.projects project
            WHERE project.id = p_project_id
            UNION ALL
            SELECT stored.principal
            FROM public.project_storage_principals stored
            WHERE stored.project_id = p_project_id
            UNION ALL
            SELECT COALESCE(upload.created_by::text, p_project_id)
            FROM public.uploads upload
            WHERE upload.project_id = p_project_id
        ) candidate
        WHERE candidate.principal IS NOT NULL
          AND candidate.principal ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
    ) source;
$function$;

-- Same report keys; retired Scope-sync state is no longer a dependent.
CREATE OR REPLACE FUNCTION public.repository_target_integrity_report()
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO 'pg_catalog', 'public', 'pg_temp' AS $function$
SELECT jsonb_build_object(
    'empty_scope_paths', (
        SELECT count(*) FROM public.repository_scopes WHERE path = ''
    ),
    'orphan_surface_targets', (
        SELECT count(*)
        FROM public.access_surfaces s
        LEFT JOIN public.projects p
          ON p.id = s.project_id AND p.org_id = s.org_id
        LEFT JOIN public.repository_scopes rs
          ON rs.id = s.scope_id AND rs.project_id = s.project_id
        WHERE p.id IS NULL OR (s.scope_id IS NOT NULL AND rs.id IS NULL)
    ),
    'credential_target_mismatches', (
        SELECT count(*)
        FROM public.access_surface_credentials c
        LEFT JOIN public.access_surfaces s
          ON s.id = c.access_surface_id
         AND s.project_id = c.project_id
         AND s.org_id = c.org_id
        LEFT JOIN public.org_members om
          ON om.org_id = c.org_id AND om.user_id = c.user_id
        WHERE s.id IS NULL
           OR (c.credential_lifecycle = 'user' AND om.id IS NULL)
    ),
    'duplicate_builtin_targets', (
        SELECT count(*) FROM (
            SELECT project_id, scope_id, kind
            FROM public.access_surfaces
            WHERE kind IN ('git_remote', 'cli', 'filesystem')
            GROUP BY project_id, scope_id, kind
            HAVING count(*) > 1
        ) duplicates
    ),
    'legacy_agent_scope_configs', (
        SELECT count(*)
        FROM public.access_surfaces
        WHERE kind = 'agent' AND config ? 'scope'
    ),
    'orphan_scope_dependents', (
        SELECT count(*)
        FROM (
            SELECT c.project_id, c.scope_id
            FROM public.synchronize_bindings c
            LEFT JOIN public.repository_scopes rs
              ON rs.id = c.scope_id AND rs.project_id = c.project_id
            WHERE c.scope_id IS NOT NULL AND rs.id IS NULL
            UNION ALL
            SELECT s.project_id, s.scope_id
            FROM public.scope_sandbox_sessions s
            LEFT JOIN public.repository_scopes rs
              ON rs.id = s.scope_id AND rs.project_id = s.project_id
            WHERE rs.id IS NULL
        ) invalid_dependents
    )
);
$function$;

DROP FUNCTION public.analytics_access_summary(text, timestamp with time zone);
DROP FUNCTION public.analytics_access_timeseries(text, timestamp with time zone, text, text, text);
DROP TABLE public.api_keys, public.sandbox_endpoints, public.agent_profiles, public.bookmarks,
  public.subscriptions, public.connectors, public.chunks, public.sync_changelog, public.sync_state,
  public.scope_sync_settings, public.scope_sync_events, public.agent_logs, public.search_index_tasks,
  public.connector_runs, public.access_logs, public.agent_execution_logs;
DROP FUNCTION public._connectors_bump_updated_at();
NOTIFY pgrst, 'reload schema';
COMMIT;
