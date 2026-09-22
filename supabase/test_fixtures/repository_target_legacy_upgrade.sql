BEGIN;

INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at
)
SELECT
    '00000000-0000-0000-0000-000000000000'::uuid,
    fixture.id,
    'authenticated',
    'authenticated',
    fixture.email,
    '',
    now(),
    '{}'::jsonb,
    '{}'::jsonb,
    now(),
    now()
FROM (VALUES
    ('00000000-0000-0000-0000-000000039001'::uuid, 'issue039-owner@example.test'),
    ('00000000-0000-0000-0000-000000039002'::uuid, 'issue039-viewer@example.test')
) AS fixture(id, email);

INSERT INTO public.organizations (
    id, name, slug, type, plan, seat_limit, created_by
) VALUES (
    'issue039-org', 'Issue 039 Upgrade', 'issue039-upgrade',
    'team', 'enterprise', 10,
    '00000000-0000-0000-0000-000000039001'::uuid
);

INSERT INTO public.org_members (id, org_id, user_id, role)
VALUES
    ('issue039-owner-membership', 'issue039-org',
     '00000000-0000-0000-0000-000000039001'::uuid, 'owner'),
    ('issue039-viewer-membership', 'issue039-org',
     '00000000-0000-0000-0000-000000039002'::uuid, 'member');

SELECT * FROM public.create_project_with_admin(
    'issue039-project',
    'Issue 039 Project',
    NULL,
    'issue039-org',
    '00000000-0000-0000-0000-000000039001'::uuid,
    'issue039-share-token'
);

INSERT INTO public.repo_scopes (
    id, project_id, name, path, exclude, mode, is_root
) VALUES
    ('issue039-root-scope', 'issue039-project', 'Root', '', '[]', 'rw', true),
    ('issue039-child-scope', 'issue039-project', 'Sales', 'company/sales',
     '["private"]', 'rw', false);

INSERT INTO public.repo_user_permissions (
    id, project_id, user_id, role, granted_by
) VALUES (
    'issue039-legacy-viewer',
    'issue039-project',
    '00000000-0000-0000-0000-000000039002'::uuid,
    'reader',
    '00000000-0000-0000-0000-000000039001'::uuid
);

INSERT INTO public.access_surfaces (
    id, org_id, project_id, scope_id, kind, name, status,
    principal_type, principal_id, config, created_by
) VALUES
    ('issue039-root-git', 'issue039-org', 'issue039-project',
     'issue039-root-scope', 'git_remote', 'Git Remote', 'active',
     'scope', 'issue039-root-scope', '{"mode":"rw","path":""}',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-root-cli', 'issue039-org', 'issue039-project',
     'issue039-root-scope', 'cli', 'FS CLI', 'active',
     'scope', 'issue039-root-scope', '{"mode":"rw","path":""}',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-root-agent', 'issue039-org', 'issue039-project',
     'issue039-root-scope', 'agent', 'Root Agent', 'active',
     'agent', 'issue039-root-agent',
     '{"scope":{"path":"company","exclude":["company/private"],"mode":"r"}}',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-child-git', 'issue039-org', 'issue039-project',
     'issue039-child-scope', 'git_remote', 'Sales Git', 'active',
     'scope', 'issue039-child-scope',
     '{"mode":"rw","path":"company/sales"}',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-child-cli', 'issue039-org', 'issue039-project',
     'issue039-child-scope', 'cli', 'Sales CLI', 'active',
     'scope', 'issue039-child-scope',
     '{"mode":"rw","path":"company/sales"}',
     '00000000-0000-0000-0000-000000039001'::uuid);

INSERT INTO public.project_workspace_bindings (
    id, org_id, project_id, scope_id, workspace_instance_id,
    bound_user_id, cloud_origin, binding_kind, mode, created_by
) VALUES
    ('issue039-root-binding', 'issue039-org', 'issue039-project',
     'issue039-root-scope', 'issue039-root-workspace-0001',
     '00000000-0000-0000-0000-000000039001'::uuid,
     'https://cloud.puppyone.test', 'full', 'rw',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-child-binding', 'issue039-org', 'issue039-project',
     'issue039-child-scope', 'issue039-child-workspace-001',
     '00000000-0000-0000-0000-000000039001'::uuid,
     'https://cloud.puppyone.test', 'scoped', 'r',
     '00000000-0000-0000-0000-000000039001'::uuid);

INSERT INTO public.access_surface_credentials (
    id, org_id, project_id, access_surface_id, workspace_binding_id,
    credential_type, grant_mode, credential_lifecycle,
    key_prefix, key_last4, key_hash, hash_alg, status, created_by
) VALUES
    ('issue039-root-credential', 'issue039-org', 'issue039-project',
     'issue039-root-git', 'issue039-root-binding',
     'git_http_token', 'rw', 'binding', 'pup_git', 'r039',
     '0390000000000000000000000000000000000000000000000000000000000001',
     'hmac_sha256_v1', 'active',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-retired-cli-credential', 'issue039-org', 'issue039-project',
     'issue039-root-cli', 'issue039-root-binding',
     'bearer_token', 'rw', 'binding', 'pup_cli', 'b039',
     '0390000000000000000000000000000000000000000000000000000000000003',
     'hmac_sha256_v1', 'active',
     '00000000-0000-0000-0000-000000039001'::uuid),
    ('issue039-child-credential', 'issue039-org', 'issue039-project',
     'issue039-child-git', NULL,
     'git_http_token', 'r', 'shared', 'pup_git', 'c039',
     '0390000000000000000000000000000000000000000000000000000000000002',
     'hmac_sha256_v1', 'active',
     '00000000-0000-0000-0000-000000039001'::uuid);

INSERT INTO public.connections (
    id, org_id, project_id, scope_id, provider, name, direction, target_path
) VALUES
    ('issue039-root-connection', 'issue039-org', 'issue039-project',
     'issue039-root-scope', 'github', 'Root Integration', 'inbound', ''),
    ('issue039-child-connection', 'issue039-org', 'issue039-project',
     'issue039-child-scope', 'github', 'Sales Integration', 'inbound',
     'company/sales');

INSERT INTO public.scope_sync_events (
    project_id, scope_id, head_version, affected_paths, source
) VALUES
    ('issue039-project', 'issue039-root-scope', 'root-head-before-cutover',
     '["README.md"]', 'publish'),
    ('issue039-project', 'issue039-child-scope', 'child-head-before-cutover',
     '["pipeline.md"]', 'publish');

INSERT INTO public.scope_sync_settings (
    project_id, scope_id, persona, auto_sync
) VALUES
    ('issue039-project', 'issue039-root-scope', 'reviewer', true),
    ('issue039-project', 'issue039-child-scope', 'dev', true);

INSERT INTO public.scope_sandbox_sessions (
    scope_id, project_id, provider, sandbox_id, state,
    created_at, last_active_at, last_state_change_at
) VALUES
    ('issue039-root-scope', 'issue039-project', 'e2b',
     'issue039-root-sandbox', 'running', 1, 1, 1),
    ('issue039-child-scope', 'issue039-project', 'e2b',
     'issue039-child-sandbox', 'running', 1, 1, 1);

COMMIT;
