BEGIN;

SELECT plan(26);

CREATE FUNCTION pg_temp.ingest_snapshot(p_project_id text)
RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
SELECT jsonb_build_object(
    'project_id', p_project_id,
    'provider_handles', '[]'::jsonb,
    'redis_keys', '[]'::jsonb,
    'cache_task_ids', '[]'::jsonb,
    'etl_task_ids', '[]'::jsonb,
    'arq_job_ids', '[]'::jsonb,
    'errors', '[]'::jsonb
)
$$;

DELETE FROM public.project_storage_inventory_batches;
DELETE FROM public.project_storage_orphan_prefixes;
UPDATE public.project_storage_inventory_state
SET inventory_complete = false,
    checkpoint = '{}'::jsonb,
    observed_object_count = 0,
    observed_multipart_count = 0,
    inventory_digest = NULL,
    verification_object_count = NULL,
    verification_multipart_count = NULL,
    verification_digest = NULL,
    completed_at = NULL,
    updated_at = now()
WHERE singleton;

INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-4000-8000-000000041001'::uuid,
    'authenticated', 'authenticated', 'lifecycle-owner@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
);

INSERT INTO public.organizations (
    id, name, slug, type, plan, seat_limit, created_by
) VALUES
    (
        'lifecycle-delete-org', 'Lifecycle Delete', 'lifecycle-delete-org',
        'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000041001'::uuid
    ),
    (
        'lifecycle-keeper-org', 'Lifecycle Keeper', 'lifecycle-keeper-org',
        'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000041001'::uuid
    );

INSERT INTO public.org_members (id, org_id, user_id, role) VALUES
    (
        'lifecycle-delete-owner', 'lifecycle-delete-org',
        '00000000-0000-4000-8000-000000041001'::uuid, 'owner'
    ),
    (
        'lifecycle-keeper-owner', 'lifecycle-keeper-org',
        '00000000-0000-4000-8000-000000041001'::uuid, 'owner'
    );

INSERT INTO public.projects (
    id, name, org_id, created_by, share_token, lifecycle_status,
    version_root_hash, mut_root_hash
) VALUES (
    'lifecycle-delete-project', 'Lifecycle Delete Project',
    'lifecycle-delete-org',
    '00000000-0000-4000-8000-000000041001'::uuid,
    'lifecycle-delete-share', 'ready',
    '4b825dc642cb6eb9a060e54bf8d69288fbee4904',
    '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
);
INSERT INTO public.project_members (
    id, project_id, org_id, user_id, role, granted_by
) VALUES (
    'lifecycle-project-owner', 'lifecycle-delete-project',
    'lifecycle-delete-org',
    '00000000-0000-4000-8000-000000041001'::uuid, 'admin',
    '00000000-0000-4000-8000-000000041001'::uuid
);

SELECT throws_ok(
    $$
    SELECT public.delete_project_control_plane(
        'lifecycle-delete-project',
        '00000000-0000-4000-8000-000000041001'::uuid,
        1800
    )
    $$,
    '55000',
    'Project storage inventory is incomplete',
    'Project deletion is fail-closed until the legacy object inventory completes'
);

SELECT is(
    public.record_project_storage_inventory_batch(
        repeat('a', 64),
        '[{"project_id":"lifecycle-delete-project","principal":"known-principal"},'
        '{"project_id":"removed-project","principal":"orphan-principal"}]'::jsonb,
        '{"objects_done":true,"uploads_done":true}'::jsonb,
        1,
        0
    )->>'outcome',
    'recorded',
    'inventory batches durably reconstruct known and orphan legacy ownership'
);
SELECT is(
    public.finalize_project_storage_inventory_scan(
        1, 0, repeat('b', 64)
    )->>'outcome',
    'finalized',
    'the first complete inventory scan records its independent proof'
);
SELECT is(
    public.verify_project_storage_inventory(
        1, 0, repeat('c', 64)
    )->>'outcome',
    'verification_failed',
    'a different second scan cannot unlock Project deletion'
);
SELECT is(
    public.verify_project_storage_inventory(
        1, 0, repeat('b', 64)
    )->>'outcome',
    'verified',
    'an identical second scan records the verification proof'
);
SELECT is(
    public.complete_project_storage_inventory()->>'outcome',
    'orphan_cleanup_required',
    'unknown historical Project prefixes keep the inventory gate closed'
);
SELECT is(
    public.mark_project_storage_orphan_cleaned(
        'removed-project', 'orphan-principal'
    ),
    true,
    'verified orphan cleanup is durably acknowledged'
);
SELECT is(
    public.complete_project_storage_inventory()->>'outcome',
    'completed',
    'matching scans plus complete orphan cleanup unlock deletion'
);

SELECT is(
    public.acquire_project_write_lease(
        'lifecycle-delete-project',
        '10000000-0000-4000-8000-000000000001'::uuid,
        'lifecycle-writer', 'test.late-write', 120,
        NULL, NULL, NULL
    )->>'outcome',
    'acquired',
    'a ready Project admits a renewable writer lease'
);
SELECT is(
    public.delete_project_control_plane(
        'lifecycle-delete-project',
        '00000000-0000-4000-8000-000000041001'::uuid,
        1800
    )->>'outcome',
    'deleted',
    'deletion atomically closes Project write admission'
);
SELECT is(
    (SELECT lifecycle_status FROM public.projects
     WHERE id = 'lifecycle-delete-project'),
    'deleting',
    'the Project remains as a hidden drain barrier'
);
SELECT is(
    public.acquire_project_write_lease(
        'lifecycle-delete-project',
        '10000000-0000-4000-8000-000000000002'::uuid,
        'late-writer', 'test.refused-write', 120,
        NULL, NULL, NULL
    )->>'outcome',
    'unavailable',
    'no writer may enter after the Project becomes deleting'
);

-- This principal models a writer admitted before deletion that finishes a
-- final legacy upload before releasing its lease. Drain must refresh the
-- durable manifest after the last lease, not trust the admission snapshot.
INSERT INTO public.project_storage_principals (project_id, principal)
VALUES ('lifecycle-delete-project', 'late-principal');

SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'lifecycle-drain-worker', 1, 300
    )),
    1::bigint,
    'the drain phase is immediately claimable'
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE project_id = 'lifecycle-delete-project'),
        'lifecycle-drain-worker'
    )->>'outcome',
    'waiting',
    'drain refuses relational deletion while an admitted writer is live'
);
SELECT ok(
    EXISTS (
        SELECT 1 FROM public.projects
        WHERE id = 'lifecycle-delete-project'
          AND lifecycle_status = 'deleting'
    )
    AND EXISTS (
        SELECT 1 FROM public.project_deletion_jobs
        WHERE project_id = 'lifecycle-delete-project'
          AND phase = 'drain' AND status = 'pending'
    ),
    'waiting preserves both the Project barrier and durable drain job'
);
SELECT is(
    public.release_project_write_lease(
        '10000000-0000-4000-8000-000000000001'::uuid,
        'lifecycle-writer'
    ),
    true,
    'the admitted writer explicitly releases its lease'
);
UPDATE public.project_deletion_jobs
SET available_at = now()
WHERE project_id = 'lifecycle-delete-project';
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'lifecycle-drain-worker', 1, 300
    )),
    1::bigint,
    'the drained lease makes the same job claimable again'
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE project_id = 'lifecycle-delete-project'),
        'lifecycle-drain-worker'
    )->>'outcome',
    'snapshot_required',
    'zero leases retain relational ownership until external snapshots persist'
);
SELECT public.persist_project_deletion_external_ingest_snapshot(
    (SELECT id FROM public.project_deletion_jobs
     WHERE project_id = 'lifecycle-delete-project'),
    'lifecycle-drain-worker',
    pg_temp.ingest_snapshot('lifecycle-delete-project')
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE project_id = 'lifecycle-delete-project'),
        'lifecycle-drain-worker'
    )->>'outcome',
    'drained',
    'zero active leases linearizes relational deletion'
);
SELECT ok(
    NOT EXISTS (
        SELECT 1 FROM public.projects WHERE id = 'lifecycle-delete-project'
    )
    AND EXISTS (
        SELECT 1 FROM public.project_deletion_jobs
        WHERE project_id = 'lifecycle-delete-project'
          AND phase = 'purge' AND status = 'pending'
          AND storage_principals ? 'late-principal'
          AND object_prefixes ?
              'users/late-principal/raw/lifecycle-delete-project/'
    ),
    'drain refreshes late ownership before cascades and advances to purge'
);
SELECT is(
    public.delete_empty_organization_control_plane(
        'lifecycle-delete-org',
        '00000000-0000-4000-8000-000000041001'::uuid
    )->>'outcome',
    'organization_deletion_in_progress',
    'Organization deletion waits for external Project cleanup verification'
);
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'lifecycle-purge-worker', 1, 300
    )),
    1::bigint,
    'the purger claims the drained Project job'
);
SELECT is(
    public.schedule_project_deletion_verification(
        (SELECT id FROM public.project_deletion_jobs
         WHERE project_id = 'lifecycle-delete-project'),
        'lifecycle-purge-worker', 10
    ),
    true,
    'purge schedules a separate quiet verification observation'
);
UPDATE public.project_deletion_jobs
SET available_at = now()
WHERE project_id = 'lifecycle-delete-project';
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'lifecycle-verify-worker', 1, 300
    )),
    1::bigint,
    'verification is claimed only as its own persisted phase'
);
SELECT is(
    public.complete_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE project_id = 'lifecycle-delete-project'),
        'lifecycle-verify-worker'
    ),
    true,
    'a verified empty observation completes the deletion tombstone'
);
SELECT is(
    public.delete_empty_organization_control_plane(
        'lifecycle-delete-org',
        '00000000-0000-4000-8000-000000041001'::uuid
    )->>'outcome',
    'deleted',
    'Organization deletion succeeds only after every Project job completes'
);

SELECT * FROM finish();

ROLLBACK;
