DO $$
DECLARE
    report jsonb;
BEGIN
    IF to_regclass('public.project_workspace_bindings') IS NOT NULL THEN
        RAISE EXCEPTION 'local checkout registration table survived final migration';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'access_surface_credentials'
          AND column_name = 'workspace_binding_id'
    ) THEN
        RAISE EXCEPTION 'checkout identity survived on Git credentials';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.access_surface_credentials
        WHERE id = 'issue039-root-credential'
          AND project_id = 'issue039-project'
          AND user_id = '00000000-0000-0000-0000-000000039001'::uuid
          AND credential_lifecycle = 'user'
          AND credential_type = 'git_http_token'
          AND grant_mode = 'rw'
          AND status = 'active'
          AND key_hash =
              '0390000000000000000000000000000000000000000000000000000000000001'
    ) THEN
        RAISE EXCEPTION 'legacy Git credential was not preserved as a user credential';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.access_surface_credentials
        WHERE id = 'issue039-retired-cli-credential'
    ) THEN
        RAISE EXCEPTION 'obsolete checkout-scoped CLI principal survived final migration';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.resolve_git_runtime_credential(
            '0390000000000000000000000000000000000000000000000000000000000001'
        )
        WHERE project_id = 'issue039-project'
          AND target_kind = 'project_root'
          AND scope_id IS NULL
          AND user_id = '00000000-0000-0000-0000-000000039001'::uuid
          AND effective_mode = 'rw'
    ) THEN
        RAISE EXCEPTION 'preserved user Git credential does not resolve to Project root';
    END IF;

    SELECT public.repository_target_integrity_report() INTO report;
    IF EXISTS (
        SELECT 1 FROM jsonb_each_text(report) item
        WHERE item.value::bigint <> 0
    ) THEN
        RAISE EXCEPTION 'final repository integrity report failed: %', report;
    END IF;
END;
$$;
