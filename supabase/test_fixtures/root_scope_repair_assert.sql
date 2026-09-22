DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.repo_scopes
        WHERE project_id = 'issue039-missing-root-project'
          AND is_root = true
          AND path = ''
          AND exclude = '[]'::jsonb
          AND mode = 'rw'
    ) THEN
        RAISE EXCEPTION 'missing root Scope was not restored in canonical shape';
    END IF;

    IF (
        SELECT count(*)
        FROM public.repo_scopes
        WHERE project_id = 'issue039-healthy-root-project' AND is_root = true
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM public.repo_scopes
        WHERE id = 'issue039-healthy-root-scope' AND is_root = true
    ) THEN
        RAISE EXCEPTION 'repair mutated a healthy Project root Scope';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.projects p
        LEFT JOIN public.repo_scopes s
          ON s.project_id = p.id AND s.is_root = true
        GROUP BY p.id
        HAVING count(s.id) <> 1
    ) THEN
        RAISE EXCEPTION 'a Project still lacks exactly one root Scope after repair';
    END IF;

    IF (
        SELECT count(*)
        FROM public.audit_logs
        WHERE operator_id = '20260717_repair_missing_root_scopes'
          AND action = 'repository_target.root_scope.repair'
    ) <> 1 THEN
        RAISE EXCEPTION 'root Scope repair audit facts are incomplete or duplicated';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.migration_log
        WHERE name = '20260717_repair_missing_root_scopes'
          AND COALESCE((summary->>'verified')::boolean, false)
    ) THEN
        RAISE EXCEPTION 'verified root Scope repair receipt is missing';
    END IF;
END;
$$;
