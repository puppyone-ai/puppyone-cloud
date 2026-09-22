-- Project initialization is an externally retried multi-step operation.
-- Exercise the real SQL serialization, quota, idempotency, credential, and
-- deletion-tombstone contracts.  dblink gives this test two actual concurrent
-- database sessions rather than simulating races sequentially.

CREATE EXTENSION IF NOT EXISTS dblink WITH SCHEMA extensions;

BEGIN;

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

-- Rollout inventory is exercised separately; this suite tests post-inventory
-- lifecycle semantics.
UPDATE public.project_storage_inventory_state
SET inventory_complete = true, completed_at = now()
WHERE singleton;

INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-4000-8000-000000039001'::uuid,
    'authenticated', 'authenticated', 'issue039-owner@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
), (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-4000-8000-000000039002'::uuid,
    'authenticated', 'authenticated', 'issue039-member@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
);

INSERT INTO public.organizations (
    id, name, slug, type, plan, seat_limit, created_by
) VALUES
    (
        'issue039-same-key-org', 'ISSUE-039 Same Key',
        'issue039-same-key-org', 'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000039001'::uuid
    ),
    (
        'issue039-quota-org', 'ISSUE-039 Quota',
        'issue039-quota-org', 'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000039001'::uuid
    ),
    (
        'issue039-pre-root-org', 'ISSUE-039 Pre Root',
        'issue039-pre-root-org', 'team', 'enterprise', 10,
        '00000000-0000-4000-8000-000000039001'::uuid
    );

INSERT INTO public.org_members (id, org_id, user_id, role) VALUES
    (
        'issue039-member-same-key', 'issue039-same-key-org',
        '00000000-0000-4000-8000-000000039001'::uuid, 'owner'
    ),
    (
        'issue039-member-quota', 'issue039-quota-org',
        '00000000-0000-4000-8000-000000039001'::uuid, 'owner'
    ),
    (
        'issue039-member-pre-root', 'issue039-pre-root-org',
        '00000000-0000-4000-8000-000000039001'::uuid, 'owner'
    ),
    (
        'issue039-member-second', 'issue039-same-key-org',
        '00000000-0000-4000-8000-000000039002'::uuid, 'member'
    );

COMMIT;

SELECT plan(79);

CREATE TEMP TABLE issue039_results (
    lane text NOT NULL,
    result jsonb NOT NULL
);

SELECT extensions.dblink_connect(
    'issue039-c1',
    format(
        'hostaddr=%s port=%s dbname=%s user=postgres password=postgres',
        host(inet_server_addr()), current_setting('port'), current_database()
    )
);
SELECT extensions.dblink_connect(
    'issue039-c2',
    format(
        'hostaddr=%s port=%s dbname=%s user=postgres password=postgres',
        host(inet_server_addr()), current_setting('port'), current_database()
    )
);

CREATE FUNCTION pg_temp.drain_dblink(p_connection text)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- libpq requires one final empty result after every async query before
    -- the same connection can accept another command.
    PERFORM result
    FROM extensions.dblink_get_result(p_connection) AS response(result jsonb);
END;
$$;

SELECT throws_ok(
    $$
    INSERT INTO public.projects (
        id, name, org_id, created_by, share_token
    ) VALUES (
        'issue039-raw-project-without-lifecycle', 'Unsafe raw insert',
        'issue039-same-key-org',
        '00000000-0000-4000-8000-000000039001'::uuid,
        'issue039-raw-project-without-lifecycle-share'
    )
    $$,
    '23502',
    'null value in column "lifecycle_status" of relation "projects" violates not-null constraint',
    'raw Project insertion cannot silently choose a published lifecycle'
);
SELECT is(
    to_regprocedure(
        'public.create_project_with_admin(text,text,text,text,uuid,text)'
    ),
    NULL::regprocedure,
    'the legacy non-idempotent Project creation RPC is absent'
);
SELECT is(
    (
        public.create_project_idempotent(
            '88888888-8888-4888-8888-888888888888', repeat('8', 64),
            '../escape', 'Unsafe Project ID', NULL,
            'issue039-pre-root-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-unsafe-id-share', 'empty', 1
        )->>'outcome'
    ),
    'invalid',
    'Project creation rejects IDs that cannot be a safe storage segment'
);

-- A permanent failure before L5 writes either root must be compensatable;
-- otherwise an invisible Project would consume quota forever.
SELECT is(
    (
        public.create_project_idempotent(
            '99999999-9999-4999-8999-999999999999', repeat('9', 64),
            'issue039-pre-root-project', 'Pre-root failure', NULL,
            'issue039-pre-root-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-pre-root-share', 'empty', 1
        )->>'outcome'
    ),
    'initializing_created',
    'pre-root Project initialization is durably journaled'
);
SELECT is(
    (
        public.abandon_project_initialization(
            'issue039-pre-root-project',
            '99999999-9999-4999-8999-999999999999',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'accepted',
    'both uninitialized roots are safely auto-abandonable'
);
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'issue039-pre-root-drain', 1, 300
    )),
    1::bigint,
    'abandon closes write admission and makes the drain phase immediately claimable'
);
SELECT public.persist_project_deletion_external_ingest_snapshot(
    (SELECT id FROM public.project_deletion_jobs
     WHERE source_operation_key = '99999999-9999-4999-8999-999999999999'),
    'issue039-pre-root-drain',
    pg_temp.ingest_snapshot('issue039-pre-root-project')
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '99999999-9999-4999-8999-999999999999'),
        'issue039-pre-root-drain'
    )->>'outcome',
    'drained',
    'an unleased abandoned Project drains before relational deletion'
);
SELECT is(
    (SELECT count(*) FROM public.projects WHERE org_id = 'issue039-pre-root-org'),
    0::bigint,
    'pre-root abandonment releases Project quota only after write drain'
);
UPDATE public.project_deletion_jobs
SET available_at = now() + interval '1 day'
WHERE source_operation_key = '99999999-9999-4999-8999-999999999999';

-- Same operation, same payload, two simultaneous requests: exactly one
-- Project is published and the loser returns the original operation snapshot.
SELECT extensions.dblink_send_query('issue039-c1', $query$
    SELECT public.create_project_idempotent(
        '11111111-1111-4111-8111-111111111111', repeat('a', 64),
        'issue039-same-key-project-a', 'Same Key Project', NULL,
        'issue039-same-key-org',
        '00000000-0000-4000-8000-000000039001'::uuid,
        'issue039-same-key-share-a', 'empty', 5
    )
$query$);
SELECT extensions.dblink_send_query('issue039-c2', $query$
    SELECT public.create_project_idempotent(
        '11111111-1111-4111-8111-111111111111', repeat('a', 64),
        'issue039-same-key-project-b', 'Same Key Project', NULL,
        'issue039-same-key-org',
        '00000000-0000-4000-8000-000000039001'::uuid,
        'issue039-same-key-share-b', 'empty', 5
    )
$query$);

INSERT INTO issue039_results
SELECT 'same-key', result
FROM extensions.dblink_get_result('issue039-c1') AS response(result jsonb);
INSERT INTO issue039_results
SELECT 'same-key', result
FROM extensions.dblink_get_result('issue039-c2') AS response(result jsonb);
SELECT pg_temp.drain_dblink('issue039-c1');
SELECT pg_temp.drain_dblink('issue039-c2');

SELECT is(
    (SELECT array_agg(result->>'outcome' ORDER BY result->>'outcome')
     FROM issue039_results WHERE lane = 'same-key'),
    ARRAY['initializing_created', 'initializing_replayed']::text[],
    'concurrent same-key creation has one creator and one resumable replay'
);
SELECT is(
    (SELECT count(*) FROM public.projects WHERE org_id = 'issue039-same-key-org'),
    1::bigint,
    'concurrent same-key creation publishes exactly one Project'
);
SELECT is(
    (SELECT count(*) FROM public.project_create_operations
     WHERE org_id = 'issue039-same-key-org'),
    1::bigint,
    'concurrent same-key creation publishes exactly one operation record'
);
SELECT is(
    (SELECT count(*) FROM public.project_members pm
     JOIN public.projects p ON p.id = pm.project_id
     WHERE p.org_id = 'issue039-same-key-org'
       AND pm.user_id = '00000000-0000-4000-8000-000000039001'::uuid
       AND pm.role = 'admin'),
    1::bigint,
    'Project and creator Admin are one publication'
);
SELECT is(
    (SELECT version_root_hash FROM public.projects
     WHERE org_id = 'issue039-same-key-org'),
    '',
    'control-plane creation leaves the initial ref write to L5'
);
SELECT is(
    (SELECT lifecycle_status FROM public.projects
     WHERE org_id = 'issue039-same-key-org'),
    'initializing',
    'the durable row remains unpublished while L5 initialization is pending'
);
SELECT is(
    (SELECT count(*) FROM public.resolve_project_role(
        (SELECT project_id FROM public.project_create_operations
         WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
        '00000000-0000-4000-8000-000000039001'::uuid
    )),
    0::bigint,
    'initializing Projects produce no ordinary authorization fact'
);
SELECT is(
    (SELECT count(*) FROM public.join_project_via_share_token(
        (SELECT share_token FROM public.projects
         WHERE org_id = 'issue039-same-key-org'),
        '00000000-0000-4000-8000-000000039002'::uuid
    )),
    0::bigint,
    'an initializing Project cannot be discovered through its share token'
);
SELECT throws_ok(
    $$
    SELECT public.issue_user_git_http_credential_idempotent(
        '11111111-1111-4111-8111-111111111111', repeat('d', 64),
        'issue039-credential-before-ready', 'issue039-surface-before-ready',
        'issue039-same-key-org',
        (SELECT project_id FROM public.project_create_operations
         WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
        NULL, '00000000-0000-4000-8000-000000039001'::uuid,
        'rw', 'pwg', 'nope', repeat('e', 64), 'hmac_sha256_v1'
    )
    $$,
    '42501',
    'Project Git credential authorization denied',
    'credential issuance fails closed until the Project is published'
);

-- Simulate the existing VersionWriteEngine.initialize_project_tree boundary;
-- the projects sync trigger mirrors this L5 fact into the legacy column.
UPDATE public.projects
SET version_root_hash = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
WHERE org_id = 'issue039-same-key-org';
SELECT is(
    (
        public.complete_project_initialization(
            '11111111-1111-4111-8111-111111111111',
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '00000000-0000-4000-8000-000000039001'::uuid
        )->>'outcome'
    ),
    'completed',
    'control-plane readiness completes only after the L5 empty-root fact exists'
);
SELECT is(
    (SELECT status FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'ready',
    'durable create operation records ready only after L5 completion'
);
SELECT is(
    (SELECT lifecycle_status FROM public.projects
     WHERE org_id = 'issue039-same-key-org'),
    'ready',
    'L5 completion atomically publishes the Project lifecycle'
);
SELECT is(
    (SELECT effective_role FROM public.resolve_project_role(
        (SELECT project_id FROM public.project_create_operations
         WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
        '00000000-0000-4000-8000-000000039001'::uuid
    )),
    'admin',
    'the completed Project becomes visible to ordinary authorization'
);
SELECT is(
    (
        public.get_project_create_operation_replay(
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            repeat('a', 64)
        )->>'outcome'
    ),
    'replayed',
    'completed response replays from the journal without a mutable source'
);

-- Different operations racing for the final quota slot serialize on the
-- Organization row.  Exactly one succeeds at projects.max = 1.
SELECT extensions.dblink_send_query('issue039-c1', $query$
    SELECT public.create_project_idempotent(
        '22222222-2222-4222-8222-222222222222', repeat('b', 64),
        'issue039-quota-project-a', 'Quota Project A', NULL,
        'issue039-quota-org',
        '00000000-0000-4000-8000-000000039001'::uuid,
        'issue039-quota-share-a', 'empty', 1
    )
$query$);
SELECT extensions.dblink_send_query('issue039-c2', $query$
    SELECT public.create_project_idempotent(
        '33333333-3333-4333-8333-333333333333', repeat('c', 64),
        'issue039-quota-project-b', 'Quota Project B', NULL,
        'issue039-quota-org',
        '00000000-0000-4000-8000-000000039001'::uuid,
        'issue039-quota-share-b', 'empty', 1
    )
$query$);

INSERT INTO issue039_results
SELECT 'quota', result
FROM extensions.dblink_get_result('issue039-c1') AS response(result jsonb);
INSERT INTO issue039_results
SELECT 'quota', result
FROM extensions.dblink_get_result('issue039-c2') AS response(result jsonb);
SELECT pg_temp.drain_dblink('issue039-c1');
SELECT pg_temp.drain_dblink('issue039-c2');

SELECT is(
    (SELECT array_agg(result->>'outcome' ORDER BY result->>'outcome')
     FROM issue039_results WHERE lane = 'quota'),
    ARRAY['capacity_exceeded', 'initializing_created']::text[],
    'concurrent quota admission accepts one operation and rejects one'
);
SELECT is(
    (SELECT count(*) FROM public.projects WHERE org_id = 'issue039-quota-org'),
    1::bigint,
    'serialized quota admission never oversubscribes the Organization'
);
SELECT is(
    (
        public.create_project_idempotent(
            (SELECT operation_key FROM public.project_create_operations
             WHERE org_id = 'issue039-quota-org'),
            (SELECT payload_hash FROM public.project_create_operations
             WHERE org_id = 'issue039-quota-org'),
            'issue039-unused-replay-id', 'ignored on replay', NULL,
            'issue039-quota-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-unused-replay-share', 'empty', 0
        )->>'outcome'
    ),
    'initializing_replayed',
    'same-key replay is resolved before a now-exhausted quota'
);
SELECT is(
    (
        public.create_project_idempotent(
            '11111111-1111-4111-8111-111111111111', repeat('f', 64),
            'issue039-unused-conflict-id', 'changed payload', NULL,
            'issue039-same-key-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-unused-conflict-share', 'empty', 5
        )->>'outcome'
    ),
    'conflict',
    'same key with a changed canonical payload is rejected'
);

-- The publish operation UUID may also key credential issuance.  The client
-- owns the secret; SQL sees and stores only the derived keyed hash.
SELECT is(
    (
        public.issue_user_git_http_credential_idempotent(
            '11111111-1111-4111-8111-111111111111', repeat('d', 64),
            'issue039-credential-a', 'issue039-surface-a',
            'issue039-same-key-org',
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            NULL, '00000000-0000-4000-8000-000000039001'::uuid,
            'rw', 'pwg', 'aaaa', repeat('e', 64), 'hmac_sha256_v1'
        )->>'outcome'
    ),
    'created',
    'first credential issuance succeeds'
);
SELECT is(
    (
        public.issue_user_git_http_credential_idempotent(
            '11111111-1111-4111-8111-111111111111', repeat('d', 64),
            'issue039-credential-never-created', 'issue039-surface-never-created',
            'issue039-same-key-org',
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            NULL, '00000000-0000-4000-8000-000000039001'::uuid,
            'rw', 'pwg', 'aaaa', repeat('e', 64), 'hmac_sha256_v1'
        )->>'outcome'
    ),
    'replayed',
    'exact credential retry replays the original operation'
);
SELECT is(
    (SELECT credential_id FROM public.git_credential_issue_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'issue039-credential-a',
    'credential replay returns the original effective credential ID'
);
SELECT is(
    (SELECT count(*) FROM public.access_surface_credentials
     WHERE id LIKE 'issue039-credential%' AND status = 'active'),
    1::bigint,
    'credential retries leave at most one effective credential'
);
SELECT is(
    (
        public.issue_user_git_http_credential_idempotent(
            '11111111-1111-4111-8111-111111111111', repeat('0', 64),
            'issue039-credential-conflict', 'issue039-surface-conflict',
            'issue039-same-key-org',
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            NULL, '00000000-0000-4000-8000-000000039001'::uuid,
            'rw', 'pwg', 'bbbb', repeat('1', 64), 'hmac_sha256_v1'
        )->>'outcome'
    ),
    'conflict',
    'changed credential payload cannot reuse the operation key'
);
SELECT hasnt_column(
    'public', 'git_credential_issue_operations', 'credential',
    'credential operation records never persist plaintext'
);

-- Abandon is fail-closed: both roots, every committed write, Scope state,
-- membership, and every other cascading Project resource must still be the
-- exact empty/bootstrap state before any compensating mutation is applied.
UPDATE public.projects
SET mut_root_hash = repeat('1', 40)
WHERE org_id = 'issue039-same-key-org';
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'a non-empty legacy/canonical root prevents initialization Abandon'
);
UPDATE public.projects
SET version_root_hash = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
WHERE org_id = 'issue039-same-key-org';

INSERT INTO public.version_transactions (
    project_id, scope_path, source_channel, intent_type, status
) VALUES (
    (SELECT project_id FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'docs', 'api', 'operation', 'committed'
);
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'a committed transaction from any channel or Scope prevents Abandon'
);
DELETE FROM public.version_transactions
WHERE project_id = (SELECT project_id FROM public.project_create_operations
                     WHERE operation_key = '11111111-1111-4111-8111-111111111111');

INSERT INTO public.version_scope_state (
    project_id, scope_path, scope_hash, head_commit_id
) VALUES (
    (SELECT project_id FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'docs', repeat('2', 40), repeat('3', 40)
);
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'non-empty repository Scope state prevents Abandon'
);
DELETE FROM public.version_scope_state
WHERE project_id = (SELECT project_id FROM public.project_create_operations
                     WHERE operation_key = '11111111-1111-4111-8111-111111111111');

INSERT INTO public.project_members (
    id, org_id, project_id, user_id, role, granted_by
) VALUES (
    'issue039-second-project-member', 'issue039-same-key-org',
    (SELECT project_id FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    '00000000-0000-4000-8000-000000039002'::uuid, 'editor',
    '00000000-0000-4000-8000-000000039001'::uuid
);
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'a second Project member prevents Abandon'
);
DELETE FROM public.project_members WHERE id = 'issue039-second-project-member';

INSERT INTO public.repository_scopes (
    id, project_id, name, path, exclude, max_mode
) VALUES (
    'issue039-user-scope',
    (SELECT project_id FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'Docs', 'docs', '[]', 'rw'
);
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'a newly introduced cascading Project resource prevents Abandon'
);
DELETE FROM public.repository_scopes WHERE id = 'issue039-user-scope';

UPDATE public.projects
SET description = 'user edited while publish was in flight'
WHERE id = (SELECT project_id FROM public.project_create_operations
            WHERE operation_key = '11111111-1111-4111-8111-111111111111');
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'a Project product-field edit makes stale initialization Abandon fail closed'
);
UPDATE public.projects project
SET description = operation.project_snapshot->>'description'
FROM public.project_create_operations operation
WHERE project.id = operation.project_id
  AND operation.operation_key = '11111111-1111-4111-8111-111111111111';

-- The exact create + standard root Surfaces + same-operation credential are
-- bootstrap resources, so the untouched initialization remains abandonable.
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'accepted',
    'empty initialization may be abandoned by its original operation'
);
SELECT is(
    (SELECT lifecycle_status FROM public.projects
     WHERE org_id = 'issue039-same-key-org'),
    'deleting',
    'abandon immediately hides the Project while retaining its drain barrier'
);
SELECT is(
    (SELECT status FROM public.access_surface_credentials
     WHERE id = 'issue039-credential-a'),
    'revoked',
    'abandon revokes the operation credential before relational drain'
);
SELECT is(
    (SELECT status FROM public.git_credential_issue_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'revoked',
    'credential operation tombstone records compensating revocation'
);
SELECT is(
    (SELECT object_prefixes FROM public.project_deletion_jobs
     WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
    public._project_deletion_object_prefixes(
        (SELECT project_id FROM public.project_create_operations
         WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
        jsonb_build_array('00000000-0000-4000-8000-000000039001')
    ),
    'deletion job covers every exact Project-owned object namespace'
);
SELECT ok(
    (SELECT phase = 'drain' AND quiescence_seconds = 1800
     FROM public.project_deletion_jobs
     WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
    'deletion job persists drain before the post-purge verification interval'
);
SELECT is(
    (
        public.create_project_idempotent(
            '11111111-1111-4111-8111-111111111111', repeat('a', 64),
            'issue039-unused-gone-id', 'Same Key Project', NULL,
            'issue039-same-key-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-unused-gone-share', 'empty', 5
        )->>'outcome'
    ),
    'gone',
    'create replay returns a durable gone tombstone after deletion'
);
SELECT is(
    (
        public.abandon_project_initialization(
            (SELECT project_id FROM public.project_create_operations
             WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
            '11111111-1111-4111-8111-111111111111',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'replayed',
    'abandon retry resolves to its original durable deletion job'
);

-- Contentful/internal creation uses the same hidden aggregate, but only its
-- orchestrator (or a reconciler holding the durable claim) may abort it. The
-- narrow user-facing empty-bootstrap Abandon path must never erase content.
SELECT is(
    (
        public.create_project_idempotent(
            '44444444-4444-4444-8444-444444444444', repeat('4', 64),
            'issue039-deferred-project', 'Deferred Project', NULL,
            'issue039-same-key-org',
            '00000000-0000-4000-8000-000000039001'::uuid,
            'issue039-deferred-share', 'deferred', 5
        )->>'outcome'
    ),
    'initializing_created',
    'contentful creation explicitly enters deferred unpublished mode'
);
SELECT ok(
    (SELECT initialization_available_at >= created_at + interval '6 hours'
     FROM public.project_create_operations
     WHERE operation_key = '44444444-4444-4444-8444-444444444444'),
    'deferred publication persists its reconciler deadline'
);
SELECT is(
    (
        public.abandon_project_initialization(
            'issue039-deferred-project',
            '44444444-4444-4444-8444-444444444444',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'not_abandonable',
    'the empty-bootstrap Abandon path cannot erase deferred content'
);

-- Keep the unrelated empty initialization out of this deterministic claim.
UPDATE public.project_create_operations
SET initialization_available_at = now() + interval '1 day'
WHERE org_id = 'issue039-quota-org' AND status = 'initializing';
UPDATE public.project_create_operations
SET initialization_available_at = now()
WHERE operation_key = '44444444-4444-4444-8444-444444444444';
SELECT is(
    (SELECT count(*) FROM public.claim_project_initialization_operations(
        'issue039-publication-worker', 10, 300
    )),
    1::bigint,
    'only a due deferred publication is claimed for reconciliation'
);
SELECT is(
    (
        public.abort_deferred_project_publication(
            'issue039-deferred-project',
            '44444444-4444-4444-8444-444444444444',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800,
            'issue039-wrong-worker'
        )->>'outcome'
    ),
    'not_abortable',
    'a worker cannot abort another worker claim'
);
SELECT is(
    (
        public.abort_deferred_project_publication(
            'issue039-deferred-project',
            '44444444-4444-4444-8444-444444444444',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800,
            'issue039-publication-worker'
        )->>'outcome'
    ),
    'accepted',
    'the owning worker atomically aborts an unpublished deferred aggregate'
);
SELECT ok(
    EXISTS (
        SELECT 1 FROM public.projects
        WHERE id = 'issue039-deferred-project'
          AND lifecycle_status = 'deleting'
    )
    AND EXISTS (
        SELECT 1 FROM public.project_create_operations
        WHERE operation_key = '44444444-4444-4444-8444-444444444444'
          AND status = 'deleted'
    )
    AND EXISTS (
        SELECT 1 FROM public.project_deletion_jobs
        WHERE project_id = 'issue039-deferred-project'
          AND source = 'publication_abort'
    ),
    'deferred abort hides authorization state behind a durable drain tombstone'
);
SELECT is(
    (
        public.abort_deferred_project_publication(
            'issue039-deferred-project',
            '44444444-4444-4444-8444-444444444444',
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800,
            'issue039-publication-worker'
        )->>'outcome'
    ),
    'replayed',
    'deferred abort retry returns its durable cleanup job'
);

-- Delete the quota winner through the same control-plane tombstone path.
UPDATE public.projects
SET version_root_hash = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
WHERE org_id = 'issue039-quota-org';
SELECT public.complete_project_initialization(
    (SELECT operation_key FROM public.project_create_operations
     WHERE org_id = 'issue039-quota-org'),
    (SELECT project_id FROM public.project_create_operations
     WHERE org_id = 'issue039-quota-org'),
    '00000000-0000-4000-8000-000000039001'::uuid
);
SELECT is(
    (
        public.delete_project_control_plane(
            (SELECT id FROM public.projects WHERE org_id = 'issue039-quota-org'),
            '00000000-0000-4000-8000-000000039001'::uuid,
            1800
        )->>'outcome'
    ),
    'deleted',
    'ordinary Project deletion uses the durable control plane'
);
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'issue039-worker-drain', 10, 300
    )),
    3::bigint,
    'all newly deleted Projects enter an immediately claimable drain phase'
);
SELECT public.persist_project_deletion_external_ingest_snapshot(
    job.id,
    'issue039-worker-drain',
    pg_temp.ingest_snapshot(job.project_id)
)
FROM public.project_deletion_jobs job
WHERE job.claimed_by = 'issue039-worker-drain'
  AND job.phase = 'drain';
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
        'issue039-worker-drain'
    )->>'outcome',
    'drained',
    'initialization Abandon drains before relational deletion'
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '44444444-4444-4444-8444-444444444444'),
        'issue039-worker-drain'
    )->>'outcome',
    'drained',
    'deferred publication abort drains before relational deletion'
);
SELECT is(
    public.drain_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE org_id = 'issue039-quota-org'),
        'issue039-worker-drain'
    )->>'outcome',
    'drained',
    'ordinary Project deletion drains before relational deletion'
);
SELECT ok(
    NOT EXISTS (
        SELECT 1 FROM public.projects
        WHERE id IN (
            'issue039-same-key-project-a',
            'issue039-same-key-project-b',
            'issue039-deferred-project'
        ) OR org_id = 'issue039-quota-org'
    ),
    'drain removes every hidden Project only after admitted writers finish'
);

UPDATE public.project_deletion_jobs
SET available_at = CASE
        WHEN source_operation_key = '11111111-1111-4111-8111-111111111111'
            THEN now()
        ELSE now() + interval '1 day'
    END
WHERE status = 'pending';
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'issue039-worker-purge', 10, 300
    )),
    1::bigint,
    'a drained Project enters the purge phase immediately'
);
SELECT is(
    public.complete_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
        'issue039-worker-purge'
    ),
    NULL::boolean,
    'a first purge can never complete deletion without verification'
);
SELECT is(
    public.schedule_project_deletion_verification(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
        'issue039-worker-purge', 10
    ),
    true,
    'successful purge schedules a separate verification phase'
);
SELECT is(
    (SELECT phase FROM public.project_deletion_jobs
     WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
    'verify',
    'deletion job persists its verify phase'
);
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'issue039-worker-verify-too-early', 10, 300
    )),
    0::bigint,
    'verification has its own quiet observation window'
);

UPDATE public.project_deletion_jobs
SET available_at = now()
WHERE source_operation_key = '11111111-1111-4111-8111-111111111111';
SELECT is(
    (SELECT count(*) FROM public.claim_project_deletion_jobs(
        'issue039-worker-verify', 10, 300
    )),
    1::bigint,
    'verification becomes claimable only after its delay'
);
SELECT is(
    public.complete_project_deletion_job(
        (SELECT id FROM public.project_deletion_jobs
         WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
        'issue039-worker-verify'
    ),
    true,
    'an empty verification observation may complete cleanup'
);
SELECT is(
    (SELECT status FROM public.project_deletion_jobs
     WHERE source_operation_key = '11111111-1111-4111-8111-111111111111'),
    'completed',
    'completed deletion retains its durable tombstone'
);
SELECT is(
    (SELECT status FROM public.project_create_operations
     WHERE operation_key = '11111111-1111-4111-8111-111111111111'),
    'deleted',
    'Project create tombstone survives the Project cascade'
);
SELECT ok(
    NOT has_function_privilege(
        'authenticated',
        'public.create_project_idempotent(text,text,text,text,text,text,uuid,text,text,integer,text,jsonb)',
        'EXECUTE'
    ),
    'human clients cannot bypass the service control plane RPC'
);
SELECT ok(
    NOT has_function_privilege(
        'service_role',
        'public.issue_user_git_http_credential(text,text,text,text,text,uuid,text,text,text,text,text)',
        'EXECUTE'
    ),
    'service callers cannot bypass idempotent user Git credential issuance'
);
SELECT ok(
    NOT has_table_privilege('service_role', 'public.projects', 'INSERT'),
    'service callers cannot directly insert a ready Project'
);
SELECT ok(
    NOT has_column_privilege(
        'service_role', 'public.projects', 'lifecycle_status', 'UPDATE'
    ),
    'service callers cannot directly publish an initializing Project'
);
SELECT ok(
    NOT has_table_privilege('service_role', 'public.project_create_operations', 'INSERT')
    AND NOT has_table_privilege('service_role', 'public.project_create_operations', 'UPDATE')
    AND NOT has_table_privilege('service_role', 'public.project_create_operations', 'DELETE'),
    'service callers cannot forge Project creation operations'
);
SELECT ok(
    NOT has_table_privilege(
        'service_role', 'public.git_credential_issue_operations', 'INSERT'
    )
    AND NOT has_table_privilege(
        'service_role', 'public.git_credential_issue_operations', 'UPDATE'
    )
    AND NOT has_table_privilege(
        'service_role', 'public.git_credential_issue_operations', 'DELETE'
    ),
    'service callers cannot forge Git credential operations'
);
SELECT ok(
    NOT has_table_privilege('service_role', 'public.project_deletion_jobs', 'INSERT')
    AND NOT has_table_privilege('service_role', 'public.project_deletion_jobs', 'UPDATE')
    AND NOT has_table_privilege('service_role', 'public.project_deletion_jobs', 'DELETE'),
    'service callers cannot forge or complete Project deletion jobs'
);

-- A downgrade that commits while deletion is waiting on the Project row must
-- win before the final role decision. This catches the former authorize-then-
-- block TOCTOU: the old order would delete with a stale Admin snapshot.
BEGIN;
INSERT INTO public.projects (
    id, name, org_id, created_by, share_token, lifecycle_status,
    version_root_hash, mut_root_hash
) VALUES (
    'issue039-delete-race-project', 'Delete authorization race',
    'issue039-same-key-org',
    '00000000-0000-4000-8000-000000039001'::uuid,
    'issue039-delete-race-share', 'ready',
    '4b825dc642cb6eb9a060e54bf8d69288fbee4904',
    '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
);
INSERT INTO public.project_members (
    id, org_id, project_id, user_id, role, granted_by
) VALUES (
    'issue039-delete-race-owner', 'issue039-same-key-org',
    'issue039-delete-race-project',
    '00000000-0000-4000-8000-000000039001'::uuid, 'admin',
    '00000000-0000-4000-8000-000000039001'::uuid
), (
    'issue039-delete-race-actor', 'issue039-same-key-org',
    'issue039-delete-race-project',
    '00000000-0000-4000-8000-000000039002'::uuid, 'admin',
    '00000000-0000-4000-8000-000000039001'::uuid
);
COMMIT;

SELECT extensions.dblink_exec('issue039-c1', 'BEGIN');
SELECT extensions.dblink_exec(
    'issue039-c1',
    $$UPDATE public.projects
      SET updated_at = updated_at
      WHERE id = 'issue039-delete-race-project'$$
);
SELECT extensions.dblink_exec(
    'issue039-c1',
    $$UPDATE public.project_members
      SET role = 'viewer'
      WHERE id = 'issue039-delete-race-actor'$$
);
SELECT extensions.dblink_send_query('issue039-c2', $query$
    SELECT public.delete_project_control_plane(
        'issue039-delete-race-project',
        '00000000-0000-4000-8000-000000039002'::uuid,
        1800
    )
$query$);
SELECT pg_sleep(0.25);
SELECT is(
    extensions.dblink_is_busy('issue039-c2'),
    1,
    'delete waits behind the Project lock while the downgrade is uncommitted'
);
SELECT extensions.dblink_exec('issue039-c1', 'COMMIT');
INSERT INTO issue039_results
SELECT 'delete-role-race', result
FROM extensions.dblink_get_result('issue039-c2') AS response(result jsonb);
SELECT pg_temp.drain_dblink('issue039-c2');
SELECT is(
    (SELECT result->>'outcome' FROM issue039_results
     WHERE lane = 'delete-role-race'),
    'forbidden',
    'delete resolves the downgraded role only after locking authorization facts'
);
SELECT ok(
    EXISTS (
        SELECT 1 FROM public.projects
        WHERE id = 'issue039-delete-race-project'
    )
    AND EXISTS (
        SELECT 1 FROM public.project_members
        WHERE id = 'issue039-delete-race-actor' AND role = 'viewer'
    )
    AND NOT EXISTS (
        SELECT 1 FROM public.project_deletion_jobs
        WHERE project_id = 'issue039-delete-race-project'
    ),
    'a concurrent revoke wins and leaves no destructive deletion tombstone'
);

DELETE FROM public.projects WHERE id = 'issue039-delete-race-project';

SELECT extensions.dblink_disconnect('issue039-c1');
SELECT extensions.dblink_disconnect('issue039-c2');

DELETE FROM public.project_deletion_jobs
WHERE org_id IN ('issue039-same-key-org', 'issue039-quota-org', 'issue039-pre-root-org');
DELETE FROM public.git_credential_issue_operations
WHERE org_id IN ('issue039-same-key-org', 'issue039-quota-org', 'issue039-pre-root-org');
DELETE FROM public.project_create_operations
WHERE org_id IN ('issue039-same-key-org', 'issue039-quota-org', 'issue039-pre-root-org');
DELETE FROM public.organizations
WHERE id IN ('issue039-same-key-org', 'issue039-quota-org', 'issue039-pre-root-org');
DELETE FROM auth.users
WHERE id IN (
    '00000000-0000-4000-8000-000000039001'::uuid,
    '00000000-0000-4000-8000-000000039002'::uuid
);

SELECT * FROM finish();
