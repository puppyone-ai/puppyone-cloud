-- Expand: align SQL Project creation admission with OrganizationOut.can_create_projects.
-- Space viewers are read-only observers and must not create Projects (which grants creator Admin).
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE OR REPLACE FUNCTION public.create_project_idempotent(
    p_operation_key text,
    p_payload_hash text,
    p_project_id text,
    p_name text,
    p_description text,
    p_org_id text,
    p_created_by uuid,
    p_share_token text,
    p_publication_mode text,
    p_project_limit integer DEFAULT NULL::integer,
    p_request_hash text DEFAULT NULL::text,
    p_result_metadata jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE
    existing_operation public.project_create_operations%ROWTYPE;
    created_project public.projects%ROWTYPE;
    current_count bigint;
    requested_name_slot bigint;
    resolved_name_slot bigint;
    resolved_project_name text;
BEGIN
    IF p_operation_key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR p_payload_hash !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_request_hash, p_payload_hash) !~ '^[0-9a-f]{64}$'
       OR p_project_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
       OR p_publication_mode NOT IN ('empty', 'deferred')
       OR jsonb_typeof(COALESCE(p_result_metadata, '{}'::jsonb)) <> 'object' THEN
        RETURN jsonb_build_object('outcome', 'invalid');
    END IF;

    -- Same-key requests serialize independently of organization quota.  This
    -- makes a durable replay observable before any capacity admission.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('project-create:' || p_created_by::text || ':' || p_operation_key, 0)
    );
    SELECT * INTO existing_operation
    FROM public.project_create_operations
    WHERE actor_user_id = p_created_by AND operation_key = p_operation_key
    FOR UPDATE;

    IF FOUND THEN
        -- request_hash is the durable identity of the caller's intent.  The
        -- payload hash may include a resolved mutable source (for example a
        -- Registry `latest` release), so a retry after source drift must
        -- replay the first admitted result instead of turning into a false
        -- idempotency conflict.
        IF existing_operation.request_hash IS DISTINCT FROM
              COALESCE(p_request_hash, p_payload_hash)
           OR existing_operation.publication_mode IS DISTINCT FROM p_publication_mode THEN
            RETURN jsonb_build_object('outcome', 'conflict');
        END IF;
        IF existing_operation.status = 'dead_lettered' THEN
            RETURN jsonb_build_object('outcome', 'dead_lettered');
        END IF;
        IF existing_operation.status = 'deleted'
           OR NOT EXISTS (
               SELECT 1 FROM public.projects p
               WHERE p.id = existing_operation.project_id
           )
           OR EXISTS (
               SELECT 1 FROM public.projects p
               WHERE p.id = existing_operation.project_id
                 AND p.lifecycle_status = 'deleting'
           ) THEN
            RETURN jsonb_build_object(
                'outcome', 'gone',
                'project_id', existing_operation.project_id
            );
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.org_members om
            WHERE om.org_id = existing_operation.org_id
              AND om.user_id = p_created_by
        ) THEN
            RETURN jsonb_build_object('outcome', 'forbidden');
        END IF;
        UPDATE public.project_create_operations
        SET replayed_at = now()
        WHERE actor_user_id = p_created_by AND operation_key = p_operation_key;
        RETURN jsonb_build_object(
            'outcome', CASE existing_operation.status
                WHEN 'ready' THEN 'replayed'
                ELSE 'initializing_replayed'
            END,
            'project', existing_operation.project_snapshot,
            'result_metadata', existing_operation.result_metadata
        );
    END IF;

    -- Lock the organization row so every new Project admission for one tenant
    -- observes a serialized Project count.  Same-key replay already returned.
    -- Only Space owners and members may create new Projects; viewers are read-only.
    PERFORM 1 FROM public.organizations WHERE id = p_org_id FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM public.org_members om
        WHERE om.org_id = p_org_id
          AND om.user_id = p_created_by
          AND om.role IN ('owner', 'member')
    ) THEN
        RETURN jsonb_build_object('outcome', 'forbidden');
    END IF;

    SELECT count(*) INTO current_count
    FROM public.projects p
    WHERE p.org_id = p_org_id;
    IF p_project_limit IS NOT NULL AND current_count >= p_project_limit THEN
        RETURN jsonb_build_object(
            'outcome', 'capacity_exceeded',
            'current', current_count,
            'maximum', p_project_limit
        );
    END IF;

    resolved_project_name := p_name;
    requested_name_slot := public._untitled_project_slot(p_name);
    IF requested_name_slot IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM public.projects project
            WHERE project.org_id = p_org_id
              AND public._untitled_project_slot(project.name) = requested_name_slot
        ) THEN
            SELECT candidate.slot INTO resolved_name_slot
            FROM generate_series(1::bigint, current_count + 1) AS candidate(slot)
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.projects project
                WHERE project.org_id = p_org_id
                  AND public._untitled_project_slot(project.name) = candidate.slot
            )
            ORDER BY candidate.slot
            LIMIT 1;
        ELSE
            resolved_name_slot := requested_name_slot;
        END IF;
        resolved_project_name := CASE resolved_name_slot
            WHEN 1 THEN 'Untitled Project'
            ELSE 'Untitled Project ' || resolved_name_slot::text
        END;
    END IF;

    -- Root refs deliberately remain uninitialized here.  The existing L5
    -- VersionWriteEngine.initialize_project_tree entry point owns the
    -- canonical empty-root write; this transaction prepares only Project,
    -- creator Admin, and the durable initialization operation.
    INSERT INTO public.projects (
        id, name, description, org_id, created_by, share_token,
        lifecycle_status
    ) VALUES (
        p_project_id, resolved_project_name, p_description, p_org_id, p_created_by,
        p_share_token, 'initializing'
    ) RETURNING * INTO created_project;

    INSERT INTO public.project_members (
        id, org_id, project_id, user_id, role, granted_by
    ) VALUES (
        gen_random_uuid()::text, p_org_id, p_project_id,
        p_created_by, 'admin', p_created_by
    );

    INSERT INTO public.project_create_operations (
        actor_user_id, operation_key, payload_hash, request_hash, org_id, project_id,
        project_snapshot, result_metadata, publication_mode,
        initialization_available_at, initialization_deadline_at
    ) VALUES (
        p_created_by, p_operation_key, p_payload_hash,
        COALESCE(p_request_hash, p_payload_hash), p_org_id, p_project_id,
        to_jsonb(created_project), COALESCE(p_result_metadata, '{}'::jsonb),
        p_publication_mode,
        CASE p_publication_mode
            WHEN 'deferred' THEN now() + interval '6 hours'
            ELSE now()
        END,
        CASE p_publication_mode
            WHEN 'deferred' THEN now() + interval '6 hours'
            ELSE now() + interval '24 hours'
        END
    );

    RETURN jsonb_build_object(
        'outcome', 'initializing_created',
        'project', to_jsonb(created_project),
        'result_metadata', COALESCE(p_result_metadata, '{}'::jsonb)
    );
END;
$_$;

REVOKE ALL ON FUNCTION public.create_project_idempotent(
    text, text, text, text, text, text, uuid, text, text, integer, text, jsonb
) FROM PUBLIC, anon, authenticated;
GRANT ALL ON FUNCTION public.create_project_idempotent(
    text, text, text, text, text, text, uuid, text, text, integer, text, jsonb
) TO service_role;

COMMIT;
