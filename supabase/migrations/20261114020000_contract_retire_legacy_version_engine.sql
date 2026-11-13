-- Retire the pre-native version engine: its tables, views, dual-written columns,
-- publishers, outbox and migration-source functions. Native refs and Git objects
-- remain the only version authority; pre-native history stays in the verified
-- recovery archives. Promote only with its verified data artifact.
-- requires-data-migration: 20261114_retire_legacy_version_engine
-- data-migration-checksum: a8f1e7aa5a2f386d6ad1be815252360cac25a4ef1bf7a3b99703523a330c99a4
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
LOCK TABLE public.projects, public.synchronize_github_logs, public.project_write_leases,
  public.version_commits, public.version_scope_state, public.version_view_commits,
  public.version_outbox, public.version_conflicts, public.version_transactions,
  public.version_refs, public.local_shadow_snapshots,
  public.version_project_root_integrity_incidents IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
  IF (EXISTS (SELECT 1 FROM public.version_commits) OR EXISTS (SELECT 1 FROM public.version_scope_state) OR EXISTS (SELECT 1 FROM public.version_view_commits) OR EXISTS (SELECT 1 FROM public.version_outbox) OR EXISTS (SELECT 1 FROM public.version_conflicts) OR EXISTS (SELECT 1 FROM public.version_transactions) OR EXISTS (SELECT 1 FROM public.version_refs) OR EXISTS (SELECT 1 FROM public.local_shadow_snapshots))
     AND NOT EXISTS (
       SELECT 1 FROM public.migration_log WHERE name = '20261114_retire_legacy_version_engine'
       AND coalesce((summary->>'verified')::boolean, false)
       AND summary->>'artifact_checksum' = 'a8f1e7aa5a2f386d6ad1be815252360cac25a4ef1bf7a3b99703523a330c99a4'
     ) THEN
    RAISE EXCEPTION 'DATA_MIGRATION_REQUIRED:20261114_retire_legacy_version_engine';
  END IF;
  IF EXISTS (
    SELECT 1 FROM (
      SELECT project_id FROM public.version_commits
      UNION ALL SELECT project_id FROM public.version_scope_state
      UNION ALL SELECT project_id FROM public.version_view_commits
      UNION ALL SELECT project_id FROM public.version_outbox
      UNION ALL SELECT project_id FROM public.version_conflicts
      UNION ALL SELECT project_id FROM public.version_transactions
      UNION ALL SELECT project_id FROM public.version_refs
      UNION ALL SELECT project_id FROM public.local_shadow_snapshots
    ) legacy_row
    WHERE NOT EXISTS (
      SELECT 1 FROM public.version_repository_archives archive
      WHERE archive.project_id = legacy_row.project_id
        AND archive.state = 'verified' AND archive.manifest_sha256 IS NOT NULL
        AND archive.verified_at IS NOT NULL AND archive.source_deleted_at IS NOT NULL
    )
  ) THEN
    RAISE EXCEPTION 'LEGACY_HISTORY_NOT_ARCHIVED';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.projects project
    WHERE project.version_root_hash IS DISTINCT FROM project.mut_root_hash
      AND NOT EXISTS (SELECT 1 FROM public.version_repositories repository
                      WHERE repository.project_id = project.id AND repository.authority = 'native')
  ) OR EXISTS (
    SELECT 1 FROM public.synchronize_github_logs WHERE version_commit_id IS DISTINCT FROM mut_commit_id
  ) THEN
    RAISE EXCEPTION 'LEGACY_COPY_MISMATCH';
  END IF;
  IF EXISTS (SELECT 1 FROM public.project_write_leases WHERE expires_at > clock_timestamp()) THEN
    RAISE EXCEPTION 'migration_writers_not_drained';
  END IF;
END $$;

-- Dual-write triggers go first; the root fence keeps guarding version_root_hash.
DROP TRIGGER projects_sync_version_columns ON public.projects;
DROP TRIGGER synchronize_github_logs_sync_version_columns ON public.synchronize_github_logs;
DROP TRIGGER zz_version_repository_root_fence ON public.projects;

-- Live functions lose only their legacy-table and mut_* column references.
-- CREATE OR REPLACE keeps each owner, SECURITY mode and ACL.
CREATE OR REPLACE FUNCTION public._project_initialization_has_cascade_dependents(p_project_id text)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.abandon_project_initialization(p_project_id text, p_operation_key text, p_actor_user_id uuid, p_quiescence_seconds integer DEFAULT 3600, p_worker_id text DEFAULT NULL::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
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
               COALESCE(project_row.version_root_hash, '') = ''
               OR project_row.version_root_hash = empty_tree
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
$function$;

CREATE OR REPLACE FUNCTION public.complete_project_initialization(p_operation_key text, p_project_id text, p_actor_user_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
DECLARE
    operation public.project_create_operations%ROWTYPE;
    project_row public.projects%ROWTYPE;
    empty_tree constant text := '4b825dc642cb6eb9a060e54bf8d69288fbee4904';
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'project-create:' || p_actor_user_id::text || ':' || p_operation_key,
            0
        )
    );
    SELECT * INTO operation
    FROM public.project_create_operations
    WHERE actor_user_id = p_actor_user_id
      AND operation_key = p_operation_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF operation.project_id IS DISTINCT FROM p_project_id THEN
        RETURN jsonb_build_object('outcome', 'conflict');
    END IF;
    IF operation.status = 'deleted' THEN
        RETURN jsonb_build_object('outcome', 'gone');
    END IF;

    SELECT * INTO project_row
    FROM public.projects
    WHERE id = p_project_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'gone');
    END IF;
    IF EXISTS(SELECT 1 FROM public.version_repositories WHERE project_id=p_project_id AND authority='native') THEN
        IF NOT EXISTS(SELECT 1 FROM public.version_repository_refs WHERE project_id=p_project_id AND name=convert_to('HEAD','UTF8')) THEN
            RETURN jsonb_build_object('outcome','root_not_initialized'); END IF;
    ELSIF COALESCE(project_row.version_root_hash, '') = ''
       OR (
           operation.publication_mode = 'empty'
           AND project_row.version_root_hash <> empty_tree
       ) THEN
        RETURN jsonb_build_object('outcome', 'root_not_initialized');
    END IF;

    IF operation.status = 'ready' THEN
        IF project_row.lifecycle_status IS DISTINCT FROM 'ready' THEN
            RETURN jsonb_build_object('outcome', 'lifecycle_conflict');
        END IF;
        UPDATE public.project_create_operations
        SET replayed_at = now()
        WHERE actor_user_id = p_actor_user_id
          AND operation_key = p_operation_key;
        RETURN jsonb_build_object(
            'outcome', 'replayed',
            'project', operation.project_snapshot
        );
    END IF;

    UPDATE public.projects
    SET lifecycle_status = 'ready',
        updated_at = now()
    WHERE id = p_project_id
      AND lifecycle_status = 'initializing'
    RETURNING * INTO project_row;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'lifecycle_conflict');
    END IF;

    UPDATE public.project_create_operations
    SET status = 'ready',
        project_snapshot = to_jsonb(project_row),
        ready_at = now(),
        initialization_claimed_at = NULL,
        initialization_claimed_by = NULL,
        initialization_last_error = NULL
    WHERE actor_user_id = p_actor_user_id
      AND operation_key = p_operation_key;

    RETURN jsonb_build_object(
        'outcome', 'completed',
        'project', to_jsonb(project_row)
    );
END;
$function$;

CREATE OR REPLACE FUNCTION public._version_fence_legacy_publication()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
DECLARE
    project text;
    guarded_projects text[];
BEGIN
    IF TG_TABLE_NAME = 'projects' THEN
        IF NEW.version_root_hash IS NOT DISTINCT FROM OLD.version_root_hash THEN RETURN NEW; END IF;
        guarded_projects := ARRAY[OLD.id, NEW.id];
    ELSIF TG_OP = 'UPDATE' THEN guarded_projects := ARRAY[OLD.project_id, NEW.project_id];
    ELSIF TG_OP = 'DELETE' THEN guarded_projects := ARRAY[OLD.project_id];
    ELSE guarded_projects := ARRAY[NEW.project_id];
    END IF;
    -- Guard both sides of row reparenting; NEW alone permits escape from a
    -- native repository. Deterministic ordering also covers cross-Project moves.
    FOR project IN SELECT DISTINCT value FROM unnest(guarded_projects) value ORDER BY value LOOP
        PERFORM 1 FROM public.projects WHERE id = project FOR UPDATE;
        -- Parent deletion may cascade through old history; it is not publication.
        IF FOUND AND EXISTS (SELECT 1 FROM public.version_repositories
                             WHERE project_id = project AND authority = 'native') THEN
            RAISE EXCEPTION 'legacy_repository_publication_fenced' USING ERRCODE = '55000';
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $function$;

CREATE OR REPLACE FUNCTION public._native_migration_fence()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
DECLARE ids text[]; pid text;
BEGIN
    IF TG_TABLE_NAME='projects' THEN
        IF TG_OP='UPDATE' AND NEW.org_id IS NOT DISTINCT FROM OLD.org_id
           AND NEW.lifecycle_status IS NOT DISTINCT FROM OLD.lifecycle_status
           AND NEW.version_root_hash IS NOT DISTINCT FROM OLD.version_root_hash THEN RETURN NEW; END IF;
        ids:=ARRAY[OLD.id];
    ELSIF TG_OP='INSERT' THEN ids:=ARRAY[NEW.project_id];
    ELSIF TG_OP='DELETE' THEN ids:=ARRAY[OLD.project_id];
    ELSE ids:=ARRAY[OLD.project_id,NEW.project_id]; END IF;
    FOR pid IN SELECT DISTINCT v FROM unnest(ids) v ORDER BY v LOOP
        PERFORM 1 FROM public.projects WHERE id=pid FOR UPDATE;
        IF EXISTS(SELECT 1 FROM public.version_repository_migrations WHERE project_id=pid AND state<>'activated') THEN
            RAISE EXCEPTION 'repository_migration_fenced' USING ERRCODE='55000';
        END IF;
    END LOOP;
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $function$;

CREATE TRIGGER zz_version_repository_root_fence BEFORE UPDATE OF version_root_hash ON public.projects
    FOR EACH ROW EXECUTE FUNCTION public._version_fence_legacy_publication();

DROP VIEW public.version_activity_feed, public.mut_commits, public.mut_conflicts,
  public.mut_object_locations, public.mut_scope_state, public.mut_version_index,
  public.mut_version_outbox;
DROP TABLE public.version_conflicts, public.version_transactions, public.version_commits,
  public.version_scope_state, public.version_view_commits, public.version_outbox,
  public.version_refs, public.local_shadow_snapshots,
  public.version_project_root_integrity_incidents;
ALTER TABLE public.projects DROP COLUMN mut_root_hash;
ALTER TABLE public.synchronize_github_logs DROP COLUMN mut_commit_id;

-- Publishers, outbox workers, legacy reads, migration-source/operator entrypoints
-- and trigger functions of the dropped tables. No runtime code calls them.
DO $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT p.oid::regprocedure AS identity FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(ARRAY[
        '_native_migration_source',
        '_repository_recovery_source',
        '_version_refs_bump_updated_at',
        'activate_native_repository_migrations',
        'activate_prepared_native_repository_projects',
        'agent_run_legacy_publication_receipt',
        'begin_native_repository_migration',
        'begin_repository_recovery_archive',
        'cas_update_root_hash',
        'cas_update_scope_state',
        'claim_mut_version_outbox_batch',
        'claim_version_outbox_batch',
        'complete_mut_version_outbox',
        'complete_version_outbox',
        'count_children_batch',
        'fail_mut_version_outbox',
        'fail_version_outbox',
        'finish_repository_recovery_archive',
        'get_mut_project_write_state',
        'get_version_project_history_refs',
        'get_version_project_write_state',
        'initialize_legacy_version_project_root',
        'normalize_scope_path',
        'prepare_native_repository_migration',
        'publish_mut_project_update',
        'publish_version_project_update',
        'publish_version_project_update_checked',
        'publish_version_project_update_with_usage',
        'publish_version_project_update_with_usage_checked',
        'sync_github_version_columns',
        'sync_project_version_columns'
    ])
  LOOP
    EXECUTE format('DROP FUNCTION %s', item.identity);
  END LOOP;
END $$;

-- No CASCADE was used; prove nothing still names a retired relation or column.
DO $$
DECLARE remnants text;
BEGIN
  SELECT string_agg(p.proname, ',') INTO remnants FROM pg_proc p
  WHERE p.pronamespace = 'public'::regnamespace
    AND (p.prosrc ~ '\m(version_commits|version_scope_state|version_view_commits|version_outbox|version_conflicts|version_transactions|version_refs|local_shadow_snapshots|version_project_root_integrity_incidents|version_activity_feed|mut_commits|mut_conflicts|mut_object_locations|mut_scope_state|mut_version_index|mut_version_outbox)\M'
         OR p.prosrc ~ '\.mut_(root_hash|commit_id)\M');
  IF remnants IS NOT NULL THEN
    RAISE EXCEPTION 'retired_version_engine_dependency_remaining: %', remnants;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND column_name LIKE 'mut\_%')
     OR EXISTS (SELECT 1 FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'v')
                AND relname ~ '^mut_') THEN
    RAISE EXCEPTION 'retired_version_engine_relation_remaining';
  END IF;
END $$;
NOTIFY pgrst, 'reload schema';
COMMIT;
