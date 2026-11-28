-- Project deletion derives storage owners from retired Search Tool tasks too.
-- Record those owners where Project deletion keeps reading them, so dropping
-- search_index_tasks cannot narrow the storage it purges.
DO $$
BEGIN
  IF to_regclass('public.search_index_tasks') IS NOT NULL THEN
    INSERT INTO public.project_storage_principals (project_id, principal)
    SELECT DISTINCT task.project_id, coalesce(task.created_by::text, task.project_id)
    FROM public.search_index_tasks task
    WHERE task.project_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
      AND coalesce(task.created_by::text, task.project_id) ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
    ON CONFLICT (project_id, principal) DO NOTHING;
  END IF;
END $$;
