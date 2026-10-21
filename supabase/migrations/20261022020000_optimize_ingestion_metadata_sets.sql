-- Set-based implementation of the existing bounded page contracts. Old RPCs
-- remain compatible. No schema/data rewrite or storage format changes.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE FUNCTION public._validate_version_metadata_page(p_rows jsonb)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'ingestion_metadata_page_invalid' USING ERRCODE='22023';
 END IF;
 IF jsonb_array_length(p_rows) NOT BETWEEN 1 AND 16384
   OR octet_length(p_rows::text)>4194304
   OR (SELECT count(DISTINCT value->>'object_id') FROM jsonb_array_elements(p_rows))
      <> jsonb_array_length(p_rows) THEN
   RAISE EXCEPTION 'ingestion_metadata_page_invalid' USING ERRCODE='22023';
 END IF;
END $$;
REVOKE ALL ON FUNCTION public._validate_version_metadata_page(jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public._validate_version_metadata_page(jsonb) TO service_role;

CREATE OR REPLACE FUNCTION public.reserve_version_object_capacity_page(
    p_project_id text,p_actor text,p_pin_id uuid,p_objects jsonb,p_required boolean DEFAULT true,
    p_io_id uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE project public.projects%ROWTYPE; repo public.version_repositories%ROWTYPE; pin public.version_object_pins%ROWTYPE;
    org_capacity public.version_organization_capacity%ROWTYPE;
    capacity public.version_repository_capacity%ROWTYPE;
    new_bytes numeric := 0; new_count bigint := 0; new_ids jsonb := '[]'::jsonb;
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
    IF new_count > 0 THEN
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
    -- Time can pass while waiting on owner repair/inventory rows. No provisional
    -- allocation may survive an expired producer, even with a captured context.
    IF pin.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    RETURN jsonb_build_object('new_body_bytes',new_bytes,'new_objects',new_count);
END $$;

CREATE OR REPLACE FUNCTION public.register_version_object_location_page(
    p_project_id text, p_actor text, p_pin_id uuid, p_rows jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE repo public.version_repositories%ROWTYPE; pin public.version_object_pins%ROWTYPE;
    affected integer;
BEGIN
    repo := public._version_lock_native_repository(p_project_id);
    SELECT * INTO pin FROM public.version_object_pins WHERE id=p_pin_id FOR UPDATE;
    IF NOT FOUND OR pin.project_id IS DISTINCT FROM p_project_id OR pin.actor IS DISTINCT FROM p_actor
       OR pin.purpose <> 'publication' OR pin.state <> 'uploading'
       OR pin.object_format <> repo.object_format OR pin.generation <> repo.generation
       OR pin.gc_epoch <> repo.gc_epoch OR pin.expires_at <= clock_timestamp()
       OR repo.gc_token IS NOT NULL THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    PERFORM public._validate_version_metadata_page(p_rows);
    IF EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_rows) AS r(item)
      WHERE item->>'project_id' IS DISTINCT FROM p_project_id
        OR public._version_oid_valid(item->>'object_id',repo.object_format) IS DISTINCT FROM true
        OR jsonb_typeof(item->'offset_bytes') IS DISTINCT FROM 'number'
        OR jsonb_typeof(item->'size_bytes') IS DISTINCT FROM 'number'
        OR (item->>'offset_bytes') !~ '^(0|[1-9][0-9]*)$'
        OR (item->>'size_bytes') !~ '^[1-9][0-9]*$'
        OR (item->>'offset_bytes')::numeric > 9223372036854775807::numeric
        OR (item->>'size_bytes')::numeric > 9223372036854775807::numeric
    ) THEN RAISE EXCEPTION 'invalid_object_locations' USING ERRCODE='22023'; END IF;
    IF EXISTS (
      SELECT 1 FROM jsonb_to_recordset(p_rows)
        AS r(object_id text,pack_key text,offset_bytes bigint,size_bytes bigint)
      CROSS JOIN LATERAL (SELECT CASE WHEN left(r.pack_key,8)='chunked:' THEN
        'chunked:version/' || p_project_id || '/object-bundles/chunked/' ||
          left(r.object_id,2) || '/' || r.object_id || '/manifest-'
        ELSE 'version/' || p_project_id || '/object-bundles/' END AS prefix) p
      CROSS JOIN LATERAL (SELECT substring(r.pack_key FROM length(p.prefix)+1) AS suffix) s
      WHERE r.pack_key IS NULL OR left(r.pack_key,length(p.prefix)) IS DISTINCT FROM p.prefix
        OR CASE WHEN left(r.pack_key,8)='chunked:' THEN
          s.suffix !~ '^[0-9a-f]{64}\.json$' OR r.offset_bytes<>0
        ELSE s.suffix !~ '^[0-9a-f]{2}/[0-9a-f]{64}\.pob$'
          OR left(s.suffix,2) IS DISTINCT FROM substring(s.suffix FROM 4 FOR 2) END
    ) THEN RAISE EXCEPTION 'noncanonical_object_location' USING ERRCODE='22023'; END IF;
    -- The trusted backend physically verifies these exact immutable placements
    -- before this RPC. The lock/pin check prevents a queued old request from
    -- installing a location collected in a newer epoch. No network I/O here.
    INSERT INTO public.version_object_locations(project_id,object_id,pack_key,offset_bytes,size_bytes)
    SELECT p_project_id,r.object_id,r.pack_key,r.offset_bytes,r.size_bytes
    FROM jsonb_to_recordset(p_rows) AS r(object_id text,pack_key text,offset_bytes bigint,size_bytes bigint)
    ORDER BY r.object_id
    ON CONFLICT (project_id,object_id) DO UPDATE SET pack_key=EXCLUDED.pack_key,
        offset_bytes=EXCLUDED.offset_bytes,size_bytes=EXCLUDED.size_bytes;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF pin.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'publication_pin_unavailable' USING ERRCODE='55000';
    END IF;
    RETURN jsonb_build_object('registered',affected);
END $$;

REVOKE ALL ON FUNCTION public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid),
 public.register_version_object_location_page(text,text,uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid),
 public.register_version_object_location_page(text,text,uuid,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
