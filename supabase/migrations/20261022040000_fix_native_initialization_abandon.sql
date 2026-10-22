-- Empty native repositories and failed transfer remnants are abandonable.
-- Published refs/history and independent user resources remain protected.
-- Reuse the durable drain/purge/verify job: retain capacity until verified purge.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE OR REPLACE FUNCTION "public"."_project_initialization_has_cascade_dependents"("p_project_id" "text") RETURNS boolean
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    SET "search_path" TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE
    dependency record;
    has_rows boolean;
BEGIN
    FOR dependency IN
        SELECT
            child_namespace.nspname AS schema_name,
            child.relname AS table_name,
            string_agg(
                format(
                    'child.%I IS NOT DISTINCT FROM parent.%I',
                    child_attribute.attname,
                    parent_attribute.attname
                ),
                ' AND ' ORDER BY key_position.position
            ) AS join_predicate
        FROM pg_catalog.pg_constraint constraint_row
        JOIN pg_catalog.pg_class child
          ON child.oid = constraint_row.conrelid
        JOIN pg_catalog.pg_namespace child_namespace
          ON child_namespace.oid = child.relnamespace
        CROSS JOIN LATERAL pg_catalog.generate_subscripts(
            constraint_row.conkey,
            1
        ) AS key_position(position)
        JOIN pg_catalog.pg_attribute child_attribute
          ON child_attribute.attrelid = constraint_row.conrelid
         AND child_attribute.attnum = constraint_row.conkey[key_position.position]
        JOIN pg_catalog.pg_attribute parent_attribute
          ON parent_attribute.attrelid = constraint_row.confrelid
         AND parent_attribute.attnum = constraint_row.confkey[key_position.position]
        WHERE constraint_row.contype = 'f'
          AND constraint_row.confrelid = 'public.projects'::regclass
          AND constraint_row.confdeltype IN ('c', 'n', 'd')
          AND constraint_row.conrelid NOT IN (
              'public.project_members'::regclass,
              'public.access_surfaces'::regclass,
              'public.access_surface_credentials'::regclass,
              'public.version_scope_state'::regclass,
              'public.version_transactions'::regclass,
              -- Native publication is proved separately under the Project and
              -- repository locks. Its provisional objects are cleanup inputs.
              'public.version_repositories'::regclass,
              -- Ownership inventory and admitted I/O are consumed by the
              -- existing deletion job, not evidence of published content.
              'public.project_storage_principals'::regclass,
              'public.project_write_leases'::regclass
          )
        GROUP BY constraint_row.oid, child_namespace.nspname, child.relname
    LOOP
        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.%I AS child '
            'JOIN public.projects AS parent ON %s '
            'WHERE parent.id = $1'
            ')',
            dependency.schema_name,
            dependency.table_name,
            dependency.join_predicate
        ) INTO has_rows USING p_project_id;

        IF has_rows THEN
            RETURN true;
        END IF;
    END LOOP;

    RETURN false;
END;
$_$;

CREATE OR REPLACE FUNCTION "public"."abandon_project_initialization"("p_project_id" "text", "p_operation_key" "text", "p_actor_user_id" "uuid", "p_quiescence_seconds" integer DEFAULT 3600, "p_worker_id" "text" DEFAULT NULL::"text") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    create_operation public.project_create_operations%ROWTYPE;
    deletion_job public.project_deletion_jobs%ROWTYPE;
    project_row public.projects%ROWTYPE;
    repository public.version_repositories%ROWTYPE;
    selected_org text;
    empty_tree constant text := '4b825dc642cb6eb9a060e54bf8d69288fbee4904';
BEGIN
    -- Match creation/publication/deletion accounting lock order. The read is
    -- only a lock hint; operation identity is rechecked under its lock below.
    SELECT org_id INTO selected_org FROM public.project_create_operations
    WHERE actor_user_id = p_actor_user_id AND operation_key = p_operation_key;
    IF selected_org IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(selected_org, 0));
        PERFORM pg_advisory_xact_lock(hashtextextended(selected_org || ':storage.logical_bytes', 0));
        PERFORM 1 FROM public.organizations WHERE id = selected_org FOR UPDATE;
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('project-create:' || p_actor_user_id::text || ':' || p_operation_key, 0)
    );
    SELECT * INTO create_operation
    FROM public.project_create_operations
    WHERE actor_user_id = p_actor_user_id AND operation_key = p_operation_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF create_operation.project_id IS DISTINCT FROM p_project_id THEN
        RETURN jsonb_build_object('outcome', 'conflict');
    END IF;
    IF create_operation.publication_mode <> 'empty' THEN
        RETURN jsonb_build_object('outcome', 'not_abandonable');
    END IF;

    SELECT * INTO deletion_job
    FROM public.project_deletion_jobs
    WHERE project_id = p_project_id;
    IF create_operation.status = 'deleted' THEN
        IF deletion_job.id IS NULL THEN
            RETURN jsonb_build_object('outcome', 'gone');
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'replayed',
            'job', to_jsonb(deletion_job)
        );
    END IF;
    IF p_worker_id IS NOT NULL
       AND (
           create_operation.status <> 'initializing'
           OR create_operation.initialization_claimed_by IS DISTINCT FROM p_worker_id
       ) THEN
        RETURN jsonb_build_object('outcome', 'claim_lost');
    END IF;

    SELECT * INTO project_row
    FROM public.projects
    WHERE id = p_project_id
    FOR UPDATE;

    IF project_row.id IS NOT NULL THEN
        -- A failed transfer may have objects, locations, reservations and pins.
        -- Only publication makes them user-visible. Serialize this proof with
        -- ref commits; deletion closes admission in this same transaction.
        SELECT * INTO repository FROM public.version_repositories
        WHERE project_id = p_project_id FOR UPDATE;
        IF FOUND AND (
            repository.authority <> 'native'
            OR repository.write_state <> 'active'
            OR repository.ref_sequence <> 0
            OR (SELECT count(*) FROM public.version_repository_refs
                WHERE project_id = p_project_id) <> 1
            OR NOT EXISTS (
                SELECT 1 FROM public.version_repository_refs
                WHERE project_id = p_project_id AND name = convert_to('HEAD', 'UTF8')
                  AND target_oid IS NULL
                  AND symbolic_target = convert_to('refs/heads/' || project_row.bound_git_branch, 'UTF8')
            )
            OR EXISTS (SELECT 1 FROM public.version_ref_transactions
                       WHERE project_id = p_project_id AND result->>'status' = 'committed')
            OR EXISTS (SELECT 1 FROM public.version_reflog_entries WHERE project_id = p_project_id)
            OR EXISTS (SELECT 1 FROM public.version_repository_billing
                       WHERE project_id = p_project_id AND accounted_bytes IS DISTINCT FROM 0)
        ) THEN
            RETURN jsonb_build_object('outcome', 'not_abandonable');
        END IF;
        IF project_row.created_by IS DISTINCT FROM p_actor_user_id
           OR (
               p_worker_id IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM public.org_members om
                   WHERE om.org_id = project_row.org_id
                     AND om.user_id = p_actor_user_id
               )
           ) THEN
            RETURN jsonb_build_object('outcome', 'forbidden');
        END IF;
        IF (
               to_jsonb(project_row)
                   - ARRAY[
                       'version_root_hash', 'mut_root_hash',
                       'updated_at', 'lifecycle_status'
                   ]::text[]
             ) IS DISTINCT FROM (
               create_operation.project_snapshot
                   - ARRAY[
                       'version_root_hash', 'mut_root_hash',
                       'updated_at', 'lifecycle_status'
                   ]::text[]
             )
           OR NOT (
               (
                   COALESCE(project_row.version_root_hash, '') = ''
                   AND COALESCE(project_row.mut_root_hash, '') = ''
               )
               OR (
                   project_row.version_root_hash = empty_tree
                   AND project_row.mut_root_hash = empty_tree
               )
           )
           OR EXISTS (
               SELECT 1 FROM public.version_scope_state state
               WHERE state.project_id = p_project_id
                 AND (
                     state.scope_path <> ''
                     OR COALESCE(state.scope_hash, '') <> ''
                     OR COALESCE(state.head_commit_id, '') <> ''
                 )
           )
           OR EXISTS (
               SELECT 1 FROM public.version_transactions tx
               WHERE tx.project_id = p_project_id
                 AND tx.status = 'committed'
           )
           OR (SELECT count(*) FROM public.project_members member
               WHERE member.project_id = p_project_id) <> 1
           OR NOT EXISTS (
               SELECT 1 FROM public.project_members member
               WHERE member.project_id = p_project_id
                 AND member.org_id = project_row.org_id
                 AND member.user_id = p_actor_user_id
                 AND member.role = 'admin'
                 AND member.granted_by = p_actor_user_id
           )
           -- The only credential operation allowed during bootstrap is the
           -- user Git credential issued with the same publish operation key.
           OR EXISTS (
               SELECT 1
               FROM public.git_credential_issue_operations op
               WHERE op.project_id = p_project_id
                 AND (
                     op.actor_user_id IS DISTINCT FROM p_actor_user_id
                     OR op.operation_key IS DISTINCT FROM p_operation_key
                     OR op.org_id IS DISTINCT FROM project_row.org_id
                     OR op.status IS DISTINCT FROM 'active'
                     OR NOT EXISTS (
                         SELECT 1
                         FROM public.access_surface_credentials credential
                         JOIN public.access_surfaces surface
                           ON surface.id = credential.access_surface_id
                         WHERE credential.id = op.credential_id
                           AND credential.project_id = p_project_id
                           AND credential.org_id = project_row.org_id
                           AND credential.user_id = p_actor_user_id
                           AND credential.created_by = p_actor_user_id
                           AND credential.credential_type = 'git_http_token'
                           AND credential.credential_lifecycle = 'user'
                           AND credential.status = 'active'
                           AND credential.key_hash = op.credential_hash
                           AND surface.project_id = p_project_id
                           AND surface.org_id = project_row.org_id
                           AND surface.scope_id IS NULL
                           AND surface.kind = 'git_remote'
                     )
                 )
           )
           OR EXISTS (
               SELECT 1
               FROM public.access_surface_credentials credential
               WHERE credential.project_id = p_project_id
                 AND NOT EXISTS (
                     SELECT 1
                     FROM public.git_credential_issue_operations op
                     WHERE op.actor_user_id = p_actor_user_id
                       AND op.operation_key = p_operation_key
                       AND op.project_id = p_project_id
                       AND op.org_id = project_row.org_id
                       AND op.credential_id = credential.id
                       AND op.credential_hash = credential.key_hash
                       AND op.status = 'active'
                 )
           )
           -- Bootstrap may materialize only the standard root Git/CLI
           -- Surfaces.  Any Scope, Agent, policy, tool, or modified Surface
           -- means this Project has become a real user resource.
           OR (SELECT count(*) FROM public.access_surfaces surface
               WHERE surface.project_id = p_project_id) NOT IN (0, 2)
           OR EXISTS (
               SELECT 1 FROM public.access_surfaces surface
               WHERE surface.project_id = p_project_id
                 AND (
                     surface.org_id IS DISTINCT FROM project_row.org_id
                     OR surface.scope_id IS NOT NULL
                     OR surface.kind NOT IN ('git_remote', 'cli')
                     OR surface.name IS DISTINCT FROM CASE surface.kind
                         WHEN 'git_remote' THEN 'Git Remote'
                         WHEN 'cli' THEN 'FS CLI'
                     END
                     OR surface.status IS DISTINCT FROM 'active'
                     OR surface.principal_type IS DISTINCT FROM 'project'
                     OR surface.principal_id IS DISTINCT FROM p_project_id
                     OR surface.config IS DISTINCT FROM jsonb_build_object(
                         'mode', 'rw', 'direction', 'bidirectional'
                     )
                     OR surface.created_by IS DISTINCT FROM p_actor_user_id
                 )
           )
           OR EXISTS (
               SELECT 1
               FROM public.access_surface_policies policy
               JOIN public.access_surfaces surface
                 ON surface.id = policy.access_surface_id
               WHERE surface.project_id = p_project_id
           )
           OR EXISTS (
               SELECT 1
               FROM public.access_tools tool_binding
               JOIN public.access_surfaces surface
                 ON surface.id = tool_binding.access_surface_id
               WHERE surface.project_id = p_project_id
           )
           OR public._project_initialization_has_cascade_dependents(p_project_id)
        THEN
            RETURN jsonb_build_object('outcome', 'not_abandonable');
        END IF;
    END IF;

    UPDATE public.access_surface_credentials c
    SET status = 'revoked', revoked_at = COALESCE(c.revoked_at, now())
    FROM public.git_credential_issue_operations op
    WHERE op.actor_user_id = p_actor_user_id
      AND op.operation_key = p_operation_key
      AND op.project_id = p_project_id
      AND c.id = op.credential_id
      AND c.status = 'active';
    UPDATE public.git_credential_issue_operations
    SET status = 'revoked', revoked_at = COALESCE(revoked_at, now())
    WHERE actor_user_id = p_actor_user_id
      AND operation_key = p_operation_key
      AND project_id = p_project_id
      AND status = 'active';

    INSERT INTO public.project_deletion_jobs (
        project_id, org_id, requested_by, source, source_operation_key,
        object_prefixes, quiescence_seconds, available_at
    ) VALUES (
        p_project_id, create_operation.org_id, p_actor_user_id,
        'initialization_abandon', p_operation_key,
        jsonb_build_array(
            'version/' || p_project_id || '/',
            'projects/' || p_project_id || '/'
        ),
        GREATEST(COALESCE(p_quiescence_seconds, 3600), 1800),
        now()
    )
    ON CONFLICT (project_id) DO UPDATE
      SET updated_at = now()
    RETURNING * INTO deletion_job;

    UPDATE public.project_create_operations
    SET status = 'deleted', deleted_at = COALESCE(deleted_at, now())
    WHERE actor_user_id = p_actor_user_id AND operation_key = p_operation_key;
    UPDATE public.projects
    SET lifecycle_status = 'deleting', updated_at = now()
    WHERE id = p_project_id AND lifecycle_status IN ('initializing', 'ready');

    RETURN jsonb_build_object(
        'outcome', 'accepted',
        'job', to_jsonb(deletion_job)
    );
END;
$$;

NOTIFY pgrst, 'reload schema';
COMMIT;
