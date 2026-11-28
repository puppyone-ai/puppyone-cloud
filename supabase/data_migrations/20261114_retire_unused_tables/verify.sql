DO $$
DECLARE
  retired text[] := ARRAY[
    'api_keys', 'sandbox_endpoints', 'agent_profiles', 'bookmarks', 'subscriptions', 'connectors',
    'chunks', 'sync_changelog', 'sync_state', 'scope_sync_settings', 'scope_sync_events',
    'agent_logs', 'search_index_tasks', 'connector_runs', 'access_logs', 'agent_execution_logs'
  ];
BEGIN
  -- Rerunnable after the Contract: with the tables gone both checks are vacuous.
  -- Nested so the task query is planned only while the table exists.
  IF to_regclass('public.search_index_tasks') IS NOT NULL THEN
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
  END IF;
  -- No kept relation may reference a retired one; the Contract uses no CASCADE.
  IF EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class target ON target.oid = c.confrelid
    JOIN pg_class source ON source.oid = c.conrelid
    WHERE c.contype = 'f' AND target.relnamespace = 'public'::regnamespace
      AND target.relname = ANY(retired) AND NOT source.relname = ANY(retired)
  ) OR EXISTS (
    SELECT 1 FROM pg_depend d
    JOIN pg_rewrite r ON r.oid = d.objid
    JOIN pg_class target ON target.oid = d.refobjid
    WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
      AND target.relnamespace = 'public'::regnamespace AND target.relname = ANY(retired)
      AND r.ev_class <> target.oid
  ) THEN
    RAISE EXCEPTION 'RETIRED_TABLE_HAS_DEPENDENT';
  END IF;
END $$;
