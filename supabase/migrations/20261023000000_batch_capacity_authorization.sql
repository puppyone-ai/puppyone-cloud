-- Authorize a bounded capacity transaction once, never once per Git object.
-- Compatible RPC replacement; no data rewrite, enrollment or external I/O.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

CREATE OR REPLACE FUNCTION public._version_assert_current_repository_actor(p_project_id text,p_org_id text,p_actor text,p_write boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE subject_user_id uuid; hint public.access_surface_credentials%ROWTYPE;
    credential public.access_surface_credentials%ROWTYPE; allowed boolean; resolved record;
BEGIN
    -- Call only after Organization -> Project locking. Lock membership before
    -- Surface -> credential, matching membership/surface deletion cascades.
    -- The platform's canonical resolvers still decide roles and narrowed modes.
    IF p_actor LIKE 'initialize:%' THEN
        -- Only the already admitted initializer lease may see/write the hidden
        -- repository. Human and Runtime credentials remain denied until ready.
        IF NOT EXISTS(SELECT 1 FROM public.projects p
            JOIN public.project_create_operations o ON o.project_id=p.id AND o.actor_user_id=p.created_by
            JOIN public.project_write_leases l ON l.project_id=p.id
            JOIN public.org_members om ON om.org_id=p.org_id AND om.user_id=o.actor_user_id
            JOIN public.project_members pm ON pm.project_id=p.id AND pm.user_id=o.actor_user_id AND pm.org_id=p.org_id
            WHERE p.id=p_project_id AND p.org_id=p_org_id AND p.lifecycle_status='initializing'
              AND o.status='initializing' AND l.id::text=substring(p_actor FROM 12)
              AND l.expires_at>clock_timestamp() AND pm.role='admin') THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END IF;
        PERFORM set_config('puppyone.native_initializer',p_actor,true);
        RETURN;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project_id AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
    IF p_actor LIKE 'user:%' THEN
        BEGIN subject_user_id := substring(p_actor FROM 6)::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END;
    ELSIF p_actor LIKE 'runtime:%' THEN
        SELECT * INTO hint FROM public.access_surface_credentials
            WHERE id=substring(p_actor FROM 9) AND project_id=p_project_id AND org_id=p_org_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
        subject_user_id := hint.user_id;
    ELSE
        RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
    END IF;
    IF subject_user_id IS NOT NULL THEN
        PERFORM 1 FROM public.org_members m WHERE m.org_id=p_org_id AND m.user_id=subject_user_id FOR SHARE;
        PERFORM 1 FROM public.project_members m WHERE m.project_id=p_project_id AND m.user_id=subject_user_id FOR SHARE;
        -- Runtime resolution below already checks this user's current role.
        -- Membership rows stay locked for both paths; do not resolve twice.
        IF p_actor LIKE 'user:%' THEN
            SELECT (r.effective_role IN ('admin','editor')) INTO allowed
                FROM public.resolve_project_role(p_project_id,subject_user_id) r;
            IF NOT FOUND OR (p_write AND NOT allowed) THEN
                RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    IF p_actor LIKE 'runtime:%' THEN
        PERFORM 1 FROM public.access_surfaces s WHERE s.id=hint.access_surface_id
            AND s.project_id=p_project_id AND s.org_id=p_org_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
        SELECT * INTO credential FROM public.access_surface_credentials WHERE id=hint.id FOR SHARE;
        IF NOT FOUND OR credential.project_id IS DISTINCT FROM p_project_id
           OR credential.org_id IS DISTINCT FROM p_org_id
           OR credential.user_id IS DISTINCT FROM hint.user_id
           OR credential.access_surface_id IS DISTINCT FROM hint.access_surface_id
           OR credential.status <> 'active'
           OR (credential.expires_at IS NOT NULL AND credential.expires_at <= clock_timestamp()) THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END IF;
        SELECT * INTO resolved FROM public.resolve_git_runtime_credential(credential.key_hash) r
            WHERE r.credential_id=credential.id;
        IF NOT FOUND OR resolved.project_id IS DISTINCT FROM p_project_id
           OR resolved.org_id IS DISTINCT FROM p_org_id OR resolved.target_kind <> 'project_root'
           OR (p_write AND resolved.effective_mode <> 'rw') THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END IF;
    END IF;
END $$;

-- Internal transaction boundary. Never accepts a caller-provided authority
-- context; the returned facts live only in this RPC's PL/pgSQL stack.
CREATE FUNCTION public._version_begin_capacity_admission(p_project_id text,p_actor text,p_pin_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE admission public.version_publication_admissions%ROWTYPE;
    org text; quota jsonb; credential_expiry timestamptz; entitlement_expiry timestamptz;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.version_repository_file_policies WHERE project_id=p_project_id) THEN
        RETURN NULL; -- explicitly unenrolled low-level/migration profile
    END IF;
    PERFORM 1 FROM public.version_repository_file_policies
        WHERE project_id=p_project_id AND initialized FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'repository_file_policy_uninitialized'; END IF;
    SELECT org_id INTO org FROM public.projects WHERE id=p_project_id;
    SELECT * INTO admission FROM public.version_publication_admissions
        WHERE pin_id=p_pin_id AND project_id=p_project_id AND actor=p_actor FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'native_publication_admission_required'; END IF;
    -- The page already holds Organization -> Project -> repository/pin and
    -- capacity locks. Current actor admission locks membership -> surface ->
    -- credential. Keep all of them until this short transaction finishes.
    PERFORM public._version_assert_current_repository_actor(p_project_id,org,p_actor,true);
    PERFORM public._version_assert_write_lease(p_project_id,admission.lease_id,admission.holder_id);
    quota := public._version_file_policy_limit(org);
    IF p_actor LIKE 'runtime:%' THEN
        SELECT expires_at INTO credential_expiry FROM public.access_surface_credentials
            WHERE id=substring(p_actor FROM 9);
    END IF;
    IF quota->>'policy_source' IS DISTINCT FROM 'standalone_disabled' THEN
        SELECT effective_until INTO entitlement_expiry FROM public.organization_entitlements WHERE org_id=org;
    END IF;
    RETURN jsonb_build_object('file_limit',quota->'file_limit',
        'lease_id',admission.lease_id,'holder_id',admission.holder_id,
        'credential_expires_at',credential_expiry,'entitlement_expires_at',entitlement_expiry);
END $$;

CREATE FUNCTION public._version_finish_capacity_admission(p_project_id text,p_admission jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF p_admission IS NULL THEN RETURN; END IF;
    -- Retain the Agent execution fence as well as the ordinary write lease.
    -- Normal Git authorization is not resolved again here. Its membership,
    -- credential and surface rows are still locked by the beginning of the RPC.
    PERFORM public._version_assert_write_lease(p_project_id,
        (p_admission->>'lease_id')::uuid,p_admission->>'holder_id');
    IF (p_admission->>'credential_expires_at')::timestamptz <= clock_timestamp() THEN
        RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
    END IF;
    IF (p_admission->>'entitlement_expires_at')::timestamptz <= clock_timestamp() THEN
        RAISE EXCEPTION 'storage_billing_entitlement_invalid' USING ERRCODE='55000';
    END IF;
END $$;
REVOKE ALL ON FUNCTION public._version_begin_capacity_admission(text,text,uuid),
    public._version_finish_capacity_admission(text,jsonb) FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.reserve_version_object_capacity_page(
    p_project_id text,p_actor text,p_pin_id uuid,p_objects jsonb,p_required boolean DEFAULT true,
    p_io_id uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE project public.projects%ROWTYPE; repo public.version_repositories%ROWTYPE; pin public.version_object_pins%ROWTYPE;
    org_capacity public.version_organization_capacity%ROWTYPE;
    capacity public.version_repository_capacity%ROWTYPE;
    new_bytes numeric := 0; new_count bigint := 0; new_ids jsonb := '[]'::jsonb; admission jsonb; maximum bigint;
BEGIN
    PERFORM public._validate_version_metadata_page(p_objects);
    project := public._version_lock_admission_project(p_project_id);
    repo := public._version_lock_native_repository(p_project_id);
    SELECT * INTO pin FROM public.version_object_pins WHERE id=p_pin_id FOR SHARE;
    IF NOT FOUND OR pin.project_id IS DISTINCT FROM p_project_id OR pin.actor IS DISTINCT FROM p_actor
       OR pin.purpose <> 'publication' OR pin.state <> 'uploading'
       OR pin.object_format IS DISTINCT FROM repo.object_format OR pin.generation <> repo.generation
       OR pin.gc_epoch <> repo.gc_epoch OR repo.gc_token IS NOT NULL OR pin.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    -- A dormant low-level profile may have no capacity enrollment. A context
    -- flag can require enrollment, but cannot bypass an existing policy.
    IF p_required=false AND NOT EXISTS (SELECT 1 FROM public.version_repository_capacity WHERE project_id=p_project_id) THEN
        RETURN jsonb_build_object('new_body_bytes',0,'new_objects',0,'profile','dormant');
    END IF;
    SELECT * INTO org_capacity FROM public.version_organization_capacity WHERE org_id=project.org_id FOR UPDATE;
    IF NOT FOUND OR NOT org_capacity.initialized THEN
        RAISE EXCEPTION 'repository_capacity_uninitialized' USING ERRCODE='55000';
    END IF;
    SELECT * INTO capacity FROM public.version_repository_capacity WHERE project_id=p_project_id FOR UPDATE;
    IF NOT FOUND OR NOT capacity.initialized OR capacity.org_id IS DISTINCT FROM project.org_id THEN
        RAISE EXCEPTION 'repository_capacity_uninitialized' USING ERRCODE='55000';
    END IF;
    IF pin.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    admission := public._version_begin_capacity_admission(p_project_id,p_actor,p_pin_id);
    maximum := (admission->>'file_limit')::bigint;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_objects) AS r(item)
        WHERE public._version_oid_valid(item->>'object_id',repo.object_format) IS DISTINCT FROM true
          OR (item->>'object_kind') IS NULL OR (item->>'object_kind') NOT IN ('commit','tree','blob','tag')
          OR jsonb_typeof(item->'body_bytes') IS DISTINCT FROM 'number'
          OR (item->>'body_bytes') !~ '^(0|[1-9][0-9]*)$'
          OR (item->>'body_bytes')::numeric > 9223372036854775807::numeric
    ) THEN RAISE EXCEPTION 'invalid_capacity_objects' USING ERRCODE='22023'; END IF;
    -- Project/repository then organization capacity locks serialize all legacy
    -- and page writers/collectors. Identity validation is a set join, not one
    -- SELECT per object; no uncounted ON CONFLICT can race these totals.
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_objects)
          AS r(object_id text,object_kind text,body_bytes bigint)
        JOIN public.version_repository_object_capacity e
          ON e.project_id=p_project_id AND e.object_id=r.object_id
        WHERE e.object_kind IS DISTINCT FROM r.object_kind OR e.body_bytes<>r.body_bytes
    ) THEN RAISE EXCEPTION 'repository_object_identity_mismatch' USING ERRCODE='22023'; END IF;
    SELECT coalesce(sum(r.body_bytes::numeric),0),count(*),
           coalesce(jsonb_agg(r.object_id ORDER BY r.object_id),'[]'::jsonb)
      INTO new_bytes,new_count,new_ids
      FROM jsonb_to_recordset(p_objects) AS r(object_id text,object_kind text,body_bytes bigint)
      LEFT JOIN public.version_repository_object_capacity e
        ON e.project_id=p_project_id AND e.object_id=r.object_id
      WHERE e.object_id IS NULL;
    IF new_count > 0 AND (
        (capacity.max_body_bytes IS NOT NULL AND capacity.used_body_bytes::numeric + new_bytes > capacity.max_body_bytes)
        OR (capacity.max_objects IS NOT NULL AND capacity.used_objects::numeric + new_count > capacity.max_objects)
        OR (org_capacity.max_body_bytes IS NOT NULL AND org_capacity.used_body_bytes::numeric + new_bytes > org_capacity.max_body_bytes)
        OR (org_capacity.max_objects IS NOT NULL AND org_capacity.used_objects::numeric + new_count > org_capacity.max_objects)
    ) THEN
        RAISE EXCEPTION 'repository_capacity_exceeded' USING ERRCODE='P0001';
    END IF;
    -- Old objects remain admissible after a file-policy reduction. Identity
    -- validation above still rejects changed type/size for an existing OID.
    IF maximum IS NOT NULL AND EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_objects) AS r(object_id text,object_kind text,body_bytes bigint)
        LEFT JOIN public.version_repository_object_capacity e
          ON e.project_id=p_project_id AND e.object_id=r.object_id
        WHERE e.object_id IS NULL AND r.object_kind='blob' AND r.body_bytes>maximum
    ) THEN RAISE EXCEPTION 'file_size_limit_exceeded' USING ERRCODE='P0001'; END IF;
    IF new_count > 0 THEN
        -- Preserve the old BEFORE INSERT guard for conflict inputs too.
        IF EXISTS (SELECT 1 FROM public.version_repository_billing
                   WHERE project_id=p_project_id AND accounted_bytes IS NOT NULL)
           AND EXISTS (SELECT 1 FROM jsonb_to_recordset(p_objects) AS r(body_bytes bigint)
                       WHERE r.body_bytes>67108864) THEN
            RAISE EXCEPTION 'repository_single_object_budget_exceeded' USING ERRCODE='54000';
        END IF;
        INSERT INTO public.version_repository_object_capacity(project_id,object_id,object_kind,body_bytes,admitted_pin_id)
        SELECT p_project_id,r.object_id,r.object_kind,r.body_bytes,p_pin_id
        FROM jsonb_to_recordset(p_objects) AS r(object_id text,object_kind text,body_bytes bigint)
        ORDER BY r.object_id ON CONFLICT (project_id,object_id) DO NOTHING;
        UPDATE public.version_repository_capacity SET used_body_bytes=(used_body_bytes::numeric+new_bytes)::bigint,
            used_objects=used_objects+new_count WHERE project_id=p_project_id;
        UPDATE public.version_organization_capacity SET used_body_bytes=(used_body_bytes::numeric+new_bytes)::bigint,
            used_objects=used_objects+new_count WHERE org_id=project.org_id;
        INSERT INTO public.version_repository_capacity_events(project_id,operation_id,action,body_bytes_delta,objects_delta,object_ids)
        VALUES(p_project_id,p_pin_id,'reserve',new_bytes::bigint,new_count,new_ids);
    END IF;
    -- NULL is a trusted backend's metadata-only reconciliation of already
    -- verified bytes. Physical writers MUST supply a fresh invocation identity.
    IF p_io_id IS NOT NULL THEN
        INSERT INTO public.version_repository_capacity_inflight(project_id,object_id,pin_id,io_id)
            SELECT p_project_id,value->>'object_id',p_pin_id,p_io_id FROM jsonb_array_elements(p_objects)
            ORDER BY value->>'object_id' ON CONFLICT DO NOTHING;
    END IF;
    PERFORM public._version_finish_capacity_admission(p_project_id,admission);
    -- Time can pass while waiting on owner repair/inventory rows. No provisional
    -- allocation may survive an expired producer, even with a captured context.
    IF pin.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    RETURN jsonb_build_object('new_body_bytes',new_bytes,'new_objects',new_count);
END $$;

-- Old callers keep their 200-object bound, error and fresh default I/O identity.
-- They cannot retain an unfenced implementation after the trigger migration.
CREATE OR REPLACE FUNCTION public.reserve_version_object_capacity(
    p_project_id text,p_actor text,p_pin_id uuid,p_objects jsonb,p_required boolean DEFAULT true,
    p_io_id uuid DEFAULT gen_random_uuid()
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF jsonb_typeof(p_objects) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid_capacity_objects' USING ERRCODE='22023';
    END IF;
    IF jsonb_array_length(p_objects) NOT BETWEEN 1 AND 200
       OR (SELECT count(DISTINCT value->>'object_id') FROM jsonb_array_elements(p_objects))
          <> jsonb_array_length(p_objects) THEN
        RAISE EXCEPTION 'invalid_capacity_objects' USING ERRCODE='22023';
    END IF;
    RETURN public.reserve_version_object_capacity_page(p_project_id,p_actor,p_pin_id,p_objects,p_required,p_io_id);
END $$;

-- Applications have no direct DML on these tables. Both runtime RPCs now enter
-- the explicit batch guard above. The other insert owners are restricted
-- inventory activation (before enrollment) and verified archive finalization.
-- Their existing owner-only capabilities and source-manifest validation remain.
DROP TRIGGER version_new_blob_size_fence ON public.version_repository_object_capacity;
DROP TRIGGER version_io_admission_fence ON public.version_repository_capacity_inflight;

-- Retain the table-level object budget for privileged archive/inventory writes,
-- but inspect the affected set once. The ref-count guard is unchanged.
CREATE FUNCTION public._version_guard_object_capacity_page()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM new_capacity_rows r
        JOIN public.version_repository_billing b ON b.project_id=r.project_id
        WHERE b.accounted_bytes IS NOT NULL AND r.body_bytes>67108864
    ) THEN RAISE EXCEPTION 'repository_single_object_budget_exceeded' USING ERRCODE='54000'; END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public._version_guard_object_capacity_page() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER bare_repository_object_budget ON public.version_repository_object_capacity;
CREATE TRIGGER bare_repository_object_budget AFTER INSERT ON public.version_repository_object_capacity
    REFERENCING NEW TABLE AS new_capacity_rows
    FOR EACH STATEMENT EXECUTE FUNCTION public._version_guard_object_capacity_page();

REVOKE ALL ON FUNCTION public.reserve_version_object_capacity(text,text,uuid,jsonb,boolean,uuid),
    public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_version_object_capacity(text,text,uuid,jsonb,boolean,uuid),
    public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
