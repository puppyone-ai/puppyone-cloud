-- Canonical Git locator and user-credential database contracts.
--
-- Raw credentials never belong in SQL. These opaque hashes exercise the final
-- Project/Scope target, credential lifecycle, and current-ProjectGrant path.

BEGIN;

SELECT plan(26);

DO $$
DECLARE
    owner_id  uuid := '00000000-0000-0000-0000-000000030101';
    editor_id uuid := '00000000-0000-0000-0000-000000030102';
BEGIN
    INSERT INTO auth.users (
        instance_id, id, aud, role, email, encrypted_password,
        email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
        created_at, updated_at
    )
    SELECT
        '00000000-0000-0000-0000-000000000000'::uuid,
        fixture.id, 'authenticated', 'authenticated', fixture.email, '',
        now(), '{}'::jsonb, '{}'::jsonb, now(), now()
    FROM (VALUES
        (owner_id, 'canonical-git-owner@example.test'),
        (editor_id, 'canonical-git-editor@example.test')
    ) AS fixture(id, email);

    INSERT INTO public.organizations (
        id, name, slug, type, plan, seat_limit, created_by
    ) VALUES (
        'canonical-git-org', 'Canonical Git Test Org',
        'canonical-git-test-org', 'team', 'enterprise', 5, owner_id
    );
    INSERT INTO public.org_members (id, org_id, user_id, role)
    VALUES
        ('canonical-git-owner-member', 'canonical-git-org', owner_id, 'owner'),
        ('canonical-git-editor-member', 'canonical-git-org', editor_id, 'member');

    INSERT INTO public.projects (
        id, name, description, org_id, created_by, share_token,
        lifecycle_status
    ) VALUES (
        'canonical-git-project', 'Canonical Git Project', NULL,
        'canonical-git-org', owner_id, 'canonical-git-share-token',
        'ready'
    );
    INSERT INTO public.project_members (
        id, org_id, project_id, user_id, role, granted_by
    ) VALUES (
        'canonical-git-owner-project-member', 'canonical-git-org',
        'canonical-git-project', owner_id, 'admin', owner_id
    );
    PERFORM * FROM public.add_project_member_authorized(
        'canonical-git-project', editor_id, 'editor', owner_id
    );

    INSERT INTO public.repository_scopes (
        id, project_id, name, path, exclude, max_mode
    ) VALUES (
        'canonical-git-docs', 'canonical-git-project',
        'Docs', 'docs', '[]', 'rw'
    );

    INSERT INTO public.access_surfaces (
        id, org_id, project_id, scope_id, kind, name, status,
        principal_type, principal_id, config, created_by
    ) VALUES
        ('canonical-git-root-surface', 'canonical-git-org',
         'canonical-git-project', NULL, 'git_remote',
         'Root Git', 'active', 'project', 'canonical-git-project',
         '{"mode":"rw"}'::jsonb, owner_id),
        ('canonical-git-docs-surface', 'canonical-git-org',
         'canonical-git-project', 'canonical-git-docs', 'git_remote',
         'Docs Git', 'active', 'scope', 'canonical-git-docs',
         '{"mode":"rw"}'::jsonb, owner_id);

    PERFORM public.rotate_access_surface_git_http_token(
        'canonical-git-root-surface', 'canonical-git-org',
        'canonical-git-project', 'r', 'git', 'r001',
        'canonical-git-hash-r-v1', 'hmac_sha256_v1', owner_id, NULL
    );
    PERFORM public.rotate_access_surface_git_http_token(
        'canonical-git-root-surface', 'canonical-git-org',
        'canonical-git-project', 'rw', 'git', 'rw01',
        'canonical-git-hash-rw-v1', 'hmac_sha256_v1', owner_id, NULL
    );

    INSERT INTO public.access_surface_credentials (
        id, org_id, project_id, access_surface_id, credential_type,
        grant_mode, credential_lifecycle, key_prefix, key_last4, key_hash,
        hash_alg, status, created_by, expires_at
    ) VALUES (
        'canonical-git-session', 'canonical-git-org',
        'canonical-git-project', 'canonical-git-root-surface',
        'git_http_token', 'r', 'session', 'git', 's001',
        'canonical-git-hash-session', 'hmac_sha256_v1', 'active',
        owner_id, now() + interval '10 minutes'
    );

    PERFORM public.rotate_access_surface_git_http_token(
        'canonical-git-root-surface', 'canonical-git-org',
        'canonical-git-project', 'r', 'git', 'r002',
        'canonical-git-hash-r-v2', 'hmac_sha256_v1', owner_id, NULL
    );

    PERFORM public.issue_user_git_http_credential(
        'canonical-git-user-rw', 'unused-surface-id',
        'canonical-git-org', 'canonical-git-project', NULL, editor_id,
        'rw', 'pwg', 'u001', 'canonical-git-hash-user-rw',
        'hmac_sha256_v1'
    );
END;
$$;

SELECT has_column(
    'public', 'access_surface_credentials', 'user_id',
    'user-owned Git credential principal is persisted'
);
SELECT hasnt_column(
    'public', 'access_surface_credentials', 'workspace_binding_id',
    'credential storage has no checkout identity column'
);
SELECT hasnt_table(
    'public', 'project_workspace_bindings',
    'Cloud stores no local workspace registration table'
);
SELECT has_column(
    'public', 'access_surface_credentials', 'credential_lifecycle',
    'credential revocation domain is persisted'
);
SELECT ok(
    to_regprocedure(
        'public.issue_user_git_http_credential(text,text,text,text,text,uuid,text,text,text,text,text)'
    ) IS NOT NULL,
    'user Git issuance RPC exists'
);
SELECT ok(
    to_regprocedure('public.revoke_user_git_http_credential(text,text,uuid)') IS NOT NULL,
    'user Git revocation RPC exists'
);
SELECT ok(
    to_regprocedure('public.resolve_git_runtime_credential(text)') IS NOT NULL,
    'single Git RuntimeGrant resolver exists'
);
SELECT ok(
    NOT has_function_privilege(
        'anon', 'public.resolve_git_runtime_credential(text)', 'EXECUTE'
    ),
    'anonymous clients cannot resolve machine credentials'
);
SELECT ok(
    NOT has_function_privilege(
        'authenticated', 'public.resolve_git_runtime_credential(text)', 'EXECUTE'
    ),
    'human clients cannot resolve machine credentials directly'
);
SELECT throws_ok(
    $$INSERT INTO public.access_surface_credentials (
        id, org_id, project_id, access_surface_id, credential_type,
        grant_mode, credential_lifecycle, key_prefix, key_last4, key_hash,
        hash_alg, status
      ) VALUES (
        'canonical-git-invalid-session', 'canonical-git-org',
        'canonical-git-project', 'canonical-git-root-surface',
        'git_http_token', 'r', 'session', 'git', 'bad1',
        'canonical-git-hash-invalid-session', 'hmac_sha256_v1', 'active'
      )$$,
    'P0001',
    'session credential requires an expiry',
    'a session token cannot become an unbounded shared secret'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE key_hash = 'canonical-git-hash-r-v1'),
    'revoked', 'shared r rotation revokes the previous r slot'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE key_hash = 'canonical-git-hash-r-v2'),
    'active', 'replacement shared r credential is active'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE key_hash = 'canonical-git-hash-rw-v1'),
    'active', 'shared r rotation preserves the rw slot'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE key_hash = 'canonical-git-hash-session'),
    'active', 'shared rotation preserves a short-lived session'
);
SELECT is(
    (SELECT effective_mode FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-r-v2')),
    'r', 'read-only shared credential resolves read-only'
);
SELECT is(
    (SELECT effective_mode FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-rw-v1')),
    'rw', 'read-write shared credential resolves read-write'
);
SELECT is(
    (SELECT credential_lifecycle FROM public.access_surface_credentials
     WHERE id = 'canonical-git-user-rw'),
    'user', 'Desktop issuance creates a user credential lifecycle'
);
SELECT is(
    (SELECT user_id FROM public.access_surface_credentials
     WHERE id = 'canonical-git-user-rw'),
    '00000000-0000-0000-0000-000000030102'::uuid,
    'the credential records only its human owner'
);
SELECT is(
    (SELECT effective_mode FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-user-rw')),
    'rw', 'an Editor user credential resolves read-write'
);
SELECT lives_ok(
    $$SELECT * FROM public.update_project_member_role_authorized(
        'canonical-git-project',
        '00000000-0000-0000-0000-000000030102'::uuid,
        'viewer',
        '00000000-0000-0000-0000-000000030101'::uuid
    )$$,
    'Project role downgrade succeeds'
);
SELECT is(
    (SELECT effective_mode FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-user-rw')),
    'r', 'current Viewer access caps an existing rw credential to read-only'
);
SELECT lives_ok(
    $$SELECT public.issue_user_git_http_credential(
        'canonical-git-user-r', 'unused-surface-id-2',
        'canonical-git-org', 'canonical-git-project', NULL,
        '00000000-0000-0000-0000-000000030102'::uuid,
        'r', 'pwg', 'u002', 'canonical-git-hash-user-r',
        'hmac_sha256_v1'
    )$$,
    'a Viewer may issue a read-only user credential'
);
SELECT is(
    (SELECT effective_mode FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-user-r')),
    'r', 'the Viewer credential resolves read-only'
);
SELECT ok(
    public.revoke_user_git_http_credential(
        'canonical-git-user-r', 'canonical-git-project',
        '00000000-0000-0000-0000-000000030102'::uuid
    ),
    'the owner can revoke one exact Git credential'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE id = 'canonical-git-user-r'),
    'revoked', 'revocation changes only the credential lifecycle'
);
SELECT is_empty(
    $$SELECT * FROM public.resolve_git_runtime_credential(
        'canonical-git-hash-user-r')$$,
    'a revoked credential cannot form a RuntimeGrant'
);

SELECT * FROM finish();
ROLLBACK;
