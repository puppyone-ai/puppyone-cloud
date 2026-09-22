DO $$
DECLARE
    report jsonb;
    surface_count bigint;
BEGIN
    IF to_regclass('public.repo_scopes') IS NOT NULL THEN
        RAISE EXCEPTION 'legacy repo_scopes table still exists';
    END IF;
    IF to_regclass('public.repository_scopes') IS NULL THEN
        RAISE EXCEPTION 'repository_scopes table is missing';
    END IF;
    IF to_regclass('public.repo_user_permissions') IS NOT NULL THEN
        RAISE EXCEPTION 'duplicate Human permission source still exists';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'repository_scopes'
          AND column_name = 'is_root'
    ) THEN
        RAISE EXCEPTION 'repository_scopes.is_root still exists';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'repository_scopes'
          AND column_name IN (
              'access_key', 'access_key_hash', 'access_key_revoked_at'
          )
    ) THEN
        RAISE EXCEPTION 'credential material still exists on repository_scopes';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'project_workspace_bindings'
          AND column_name = 'binding_kind'
    ) THEN
        RAISE EXCEPTION 'project_workspace_bindings.binding_kind still exists';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.repository_scopes
        WHERE id = 'issue039-root-scope' OR path = ''
    ) THEN
        RAISE EXCEPTION 'legacy root Scope survived cutover';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.repository_scopes
        WHERE id = 'issue039-child-scope'
          AND project_id = 'issue039-project'
          AND path = 'company/sales'
          AND max_mode = 'rw'
    ) THEN
        RAISE EXCEPTION 'real child Scope was not preserved';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.access_surfaces
        WHERE id = 'issue039-root-git'
          AND project_id = 'issue039-project'
          AND scope_id IS NULL
          AND principal_type = 'project'
          AND principal_id = 'issue039-project'
          AND NOT (config ? 'path')
    ) THEN
        RAISE EXCEPTION 'root Git Surface was not mapped to Project root';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.access_surfaces
        WHERE id = 'issue039-child-git'
          AND project_id = 'issue039-project'
          AND scope_id = 'issue039-child-scope'
    ) THEN
        RAISE EXCEPTION 'child Git Surface target changed';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.project_workspace_bindings
        WHERE id = 'issue039-root-binding'
          AND scope_id IS NULL
          AND mode = 'rw'
    ) THEN
        RAISE EXCEPTION 'root Workspace Binding was not mapped';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.project_workspace_bindings
        WHERE id = 'issue039-child-binding'
          AND scope_id = 'issue039-child-scope'
          AND mode = 'r'
    ) THEN
        RAISE EXCEPTION 'child Workspace Binding target changed';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.access_surface_credentials
        WHERE id = 'issue039-root-credential'
          AND access_surface_id = 'issue039-root-git'
          AND workspace_binding_id = 'issue039-root-binding'
          AND key_hash =
              '0390000000000000000000000000000000000000000000000000000000000001'
          AND credential_lifecycle = 'binding'
          AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'root credential identity/hash/lifecycle changed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.access_surface_credentials
        WHERE id = 'issue039-retired-cli-credential'
          AND access_surface_id = 'issue039-root-cli'
          AND workspace_binding_id = 'issue039-root-binding'
          AND credential_type = 'bearer_token'
          AND credential_lifecycle = 'binding'
          AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'historical CLI registration credential was not preserved before final retirement';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.project_members
        WHERE project_id = 'issue039-project'
          AND user_id = '00000000-0000-0000-0000-000000039002'::uuid
          AND role = 'viewer'
    ) THEN
        RAISE EXCEPTION 'legacy Human permission was not migrated';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.connections
        WHERE id = 'issue039-root-connection' AND scope_id IS NULL
    ) OR NOT EXISTS (
        SELECT 1 FROM public.connections
        WHERE id = 'issue039-child-connection'
          AND scope_id = 'issue039-child-scope'
    ) THEN
        RAISE EXCEPTION 'Integration targets were not mapped exactly';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.scope_sync_events
        WHERE scope_id = 'issue039-root-scope'
    ) OR EXISTS (
        SELECT 1 FROM public.scope_sync_settings
        WHERE scope_id = 'issue039-root-scope'
    ) OR EXISTS (
        SELECT 1 FROM public.scope_sandbox_sessions
        WHERE scope_id = 'issue039-root-scope'
    ) THEN
        RAISE EXCEPTION 'retired root Scope leaked into Scope-only state';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.scope_sync_events
        WHERE scope_id = 'issue039-child-scope'
    ) OR NOT EXISTS (
        SELECT 1 FROM public.scope_sync_settings
        WHERE scope_id = 'issue039-child-scope'
    ) OR NOT EXISTS (
        SELECT 1 FROM public.scope_sandbox_sessions
        WHERE scope_id = 'issue039-child-scope'
    ) THEN
        RAISE EXCEPTION 'real Scope state was not preserved';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.access_surfaces
        WHERE id = 'issue039-root-agent'
          AND NOT (config ? 'scope')
          AND config ->> 'activated' = 'true'
          AND config #>> '{repository_view,target,kind}' = 'project_root'
          AND config #>> '{repository_view,target,project_id}' = 'issue039-project'
          AND config #>> '{bash_view,path_prefix}' = 'company'
          AND config #>> '{bash_view,max_mode}' = 'r'
    ) THEN
        RAISE EXCEPTION 'Agent target/view configuration was not normalized';
    END IF;

    INSERT INTO public.repository_scopes (
        id, project_id, name, path, exclude, max_mode
    ) VALUES (
        'issue039-explicit-surface-scope', 'issue039-project',
        'Explicit Surface Scope', 'explicit/surface', '[]', 'rw'
    );

    SELECT count(*) INTO surface_count
    FROM public.access_surfaces
    WHERE project_id = 'issue039-project'
      AND scope_id = 'issue039-explicit-surface-scope';
    IF surface_count <> 0 THEN
        RAISE EXCEPTION 'Scope creation implicitly created Access Surfaces';
    END IF;

    PERFORM public.ensure_repository_target_access_surfaces(
        'issue039-project', 'issue039-explicit-surface-scope',
        '00000000-0000-0000-0000-000000039001'::uuid,
        NULL, NULL
    );
    PERFORM public.ensure_repository_target_access_surfaces(
        'issue039-project', 'issue039-explicit-surface-scope',
        '00000000-0000-0000-0000-000000039001'::uuid,
        NULL, NULL
    );

    SELECT count(*) INTO surface_count
    FROM public.access_surfaces
    WHERE project_id = 'issue039-project'
      AND scope_id = 'issue039-explicit-surface-scope'
      AND kind IN ('git_remote', 'cli');
    IF surface_count <> 2 THEN
        RAISE EXCEPTION 'explicit Surface enable did not converge to Git + CLI';
    END IF;

    SELECT public.repository_target_integrity_report() INTO report;
    IF EXISTS (
        SELECT 1 FROM jsonb_each_text(report) item
        WHERE item.value::bigint <> 0
    ) THEN
        RAISE EXCEPTION 'repository target integrity report failed: %', report;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.migration_log
        WHERE name = '20260715_project_owned_repository_targets_preflight'
          AND COALESCE((summary ->> 'verified')::boolean, false)
          AND summary ->> 'artifact_checksum' =
              'c9c417a19b0ad2a9086588e31775e604e0eefe18d2fcb5c8c1f5ce570661ae55'
    ) THEN
        RAISE EXCEPTION 'repository target preflight receipt is missing';
    END IF;
END;
$$;
