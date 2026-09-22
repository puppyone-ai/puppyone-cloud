BEGIN;

UPDATE public.project_storage_inventory_state
SET inventory_complete = true, completed_at = now()
WHERE singleton;

SELECT plan(19);

INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at
) VALUES
    (
        '00000000-0000-0000-0000-000000000000'::uuid,
        '00000000-0000-4000-8000-000000040001'::uuid,
        'authenticated', 'authenticated', 'closure-owner@example.test', '',
        now(), '{}'::jsonb, '{}'::jsonb, now(), now()
    ),
    (
        '00000000-0000-0000-0000-000000000000'::uuid,
        '00000000-0000-4000-8000-000000040002'::uuid,
        'authenticated', 'authenticated', 'closure-contributor@example.test', '',
        now(), '{}'::jsonb, '{}'::jsonb, now(), now()
    ),
    (
        '00000000-0000-0000-0000-000000000000'::uuid,
        '00000000-0000-4000-8000-000000040003'::uuid,
        'authenticated', 'authenticated', 'closure-solo@example.test', '',
        now(), '{}'::jsonb, '{}'::jsonb, now(), now()
    );

INSERT INTO public.organizations (
    id, name, slug, type, plan, seat_limit, created_by
) VALUES
    (
        'closure-nonempty-org', 'Closure Nonempty', 'closure-nonempty-org',
        'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000040001'::uuid
    ),
    (
        'closure-empty-org', 'Closure Empty', 'closure-empty-org',
        'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000040001'::uuid
    ),
    (
        'closure-solo-org', 'Closure Solo', 'closure-solo-org',
        'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000040003'::uuid
    );

INSERT INTO public.org_members (id, org_id, user_id, role) VALUES
    (
        'closure-owner-nonempty', 'closure-nonempty-org',
        '00000000-0000-4000-8000-000000040001'::uuid, 'owner'
    ),
    (
        'closure-owner-empty', 'closure-empty-org',
        '00000000-0000-4000-8000-000000040001'::uuid, 'owner'
    ),
    (
        'closure-solo-member', 'closure-solo-org',
        '00000000-0000-4000-8000-000000040003'::uuid, 'owner'
    );

INSERT INTO public.projects (
    id, name, org_id, created_by, share_token, lifecycle_status
) VALUES (
    'closure-project', 'Closure Project', 'closure-nonempty-org',
    '00000000-0000-4000-8000-000000040001'::uuid,
    'closure-project-share', 'ready'
);
INSERT INTO public.project_members (
    id, project_id, org_id, user_id, role, granted_by
) VALUES (
    'closure-project-owner', 'closure-project', 'closure-nonempty-org',
    '00000000-0000-4000-8000-000000040001'::uuid, 'admin',
    '00000000-0000-4000-8000-000000040001'::uuid
);

INSERT INTO public.uploads (
    id, created_by, project_id, type, config, status, progress
) VALUES
    (
        'closure-upload-owner',
        '00000000-0000-4000-8000-000000040001'::uuid,
        'closure-project', 'file_ocr', '{}'::jsonb, 'completed', 100
    ),
    (
        'closure-upload-contributor',
        '00000000-0000-4000-8000-000000040002'::uuid,
        'closure-project', 'file_ocr', '{}'::jsonb, 'completed', 100
    ),
    (
        'closure-upload-legacy', NULL,
        'closure-project', 'file_ocr', '{}'::jsonb, 'completed', 100
    );

-- Deleting task metadata must not erase the only way to find historical
-- user-leading ETL keys at future Project deletion time.
DELETE FROM public.uploads WHERE id = 'closure-upload-contributor';
SELECT ok(
    EXISTS (
        SELECT 1
        FROM public.project_storage_principals
        WHERE project_id = 'closure-project'
          AND principal = '00000000-0000-4000-8000-000000040002'
    ),
    'storage-principal ownership survives independent Upload-row deletion'
);

-- Simulate the old caller payload.  The BEFORE INSERT trigger must replace it
-- with the complete manifest before any Project-owned rows can cascade away.
INSERT INTO public.project_deletion_jobs (
    id, project_id, org_id, requested_by, source, object_prefixes,
    quiescence_seconds, available_at
) VALUES (
    'closure-deletion-job', 'closure-project', 'closure-nonempty-org',
    '00000000-0000-4000-8000-000000040001'::uuid,
    'project_delete', '["version/closure-project/"]'::jsonb,
    1800, now()
);

SELECT ok(
    to_regprocedure(
        'public.delete_empty_organization_control_plane(text,uuid)'
    ) IS NOT NULL,
    'empty-Organization deletion has one guarded control-plane RPC'
);
SELECT ok(
    NOT has_table_privilege('service_role', 'public.organizations', 'DELETE'),
    'service_role cannot bypass the Organization deletion RPC'
);
SELECT ok(
    NOT has_table_privilege('service_role', 'public.projects', 'DELETE'),
    'service_role cannot bypass the Project deletion journal'
);
SELECT is(
    (SELECT storage_principals FROM public.project_deletion_jobs
     WHERE id = 'closure-deletion-job'),
    '["00000000-0000-4000-8000-000000040001",'
    '"00000000-0000-4000-8000-000000040002",'
    '"closure-project"]'::jsonb,
    'the deletion tombstone snapshots every ETL storage principal and null fallback'
);
SELECT is(
    (SELECT object_prefixes FROM public.project_deletion_jobs
     WHERE id = 'closure-deletion-job'),
    public._project_deletion_object_prefixes(
        'closure-project',
        '["00000000-0000-4000-8000-000000040001",'
        '"00000000-0000-4000-8000-000000040002",'
        '"closure-project"]'::jsonb
    ),
    'the deletion tombstone contains exactly the canonical allowlist'
);
SELECT ok(
    (SELECT object_prefixes ? 'shadow-snapshots/closure-project/'
     FROM public.project_deletion_jobs WHERE id = 'closure-deletion-job'),
    'shadow snapshot manifests are Project-owned cleanup data'
);
SELECT ok(
    (SELECT object_prefixes ?
        'users/00000000-0000-4000-8000-000000040002/etl_artifacts/closure-project/'
     FROM public.project_deletion_jobs WHERE id = 'closure-deletion-job'),
    'contributor ETL artifacts are included'
);
SELECT ok(
    (SELECT object_prefixes ? 'users/closure-project/raw/closure-project/'
     FROM public.project_deletion_jobs WHERE id = 'closure-deletion-job'),
    'legacy null-creator raw uploads are included'
);
SELECT throws_ok(
    $$
    UPDATE public.project_deletion_jobs
    SET object_prefixes = '["version/some-other-project/"]'::jsonb
    WHERE id = 'closure-deletion-job'
    $$,
    '23514',
    NULL,
    'a deletion job cannot be widened, narrowed, or redirected after admission'
);

SELECT is(
    public.delete_empty_organization_control_plane(
        'closure-nonempty-org',
        '00000000-0000-4000-8000-000000040001'::uuid
    )->>'outcome',
    'organization_not_empty',
    'Organization deletion refuses to cascade a Project'
);
SELECT ok(
    EXISTS (SELECT 1 FROM public.projects WHERE id = 'closure-project'),
    'the refused Organization delete preserves its Project'
);

SET LOCAL ROLE service_role;
SELECT throws_ok(
    $$DELETE FROM public.organizations WHERE id = 'closure-empty-org'$$,
    '42501',
    'permission denied for table organizations',
    'service_role direct Organization DELETE is rejected at the database boundary'
);
SELECT throws_ok(
    $$DELETE FROM public.projects WHERE id = 'closure-project'$$,
    '42501',
    'permission denied for table projects',
    'service_role direct Project DELETE cannot bypass the durable deletion job'
);
SELECT is(
    public.delete_empty_organization_control_plane(
        'closure-empty-org',
        '00000000-0000-4000-8000-000000040001'::uuid
    )->>'outcome',
    'deleted',
    'the SECURITY DEFINER RPC can delete after direct DELETE was revoked'
);
RESET ROLE;
SELECT ok(
    NOT EXISTS (
        SELECT 1 FROM public.organizations WHERE id = 'closure-empty-org'
    ),
    'the guarded RPC removes the empty Organization'
);
SELECT is(
    public.delete_empty_organization_control_plane(
        'closure-solo-org',
        '00000000-0000-4000-8000-000000040003'::uuid
    )->>'outcome',
    'only_organization',
    'the guarded RPC preserves an actor final Organization'
);
SELECT ok(
    EXISTS (SELECT 1 FROM public.organizations WHERE id = 'closure-solo-org'),
    'the final Organization remains durable'
);
SELECT is(
    public.delete_empty_organization_control_plane(
        'closure-nonempty-org',
        '00000000-0000-4000-8000-000000040002'::uuid
    )->>'outcome',
    'forbidden',
    'the final locked membership check rejects a non-owner'
);

SELECT * FROM finish();

ROLLBACK;
