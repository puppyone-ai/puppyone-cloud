-- Standard Git packs coexist with existing POB/chunked data.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE OR REPLACE FUNCTION public.register_version_object_locations(
    p_project_id text, p_actor text, p_pin_id uuid, p_rows jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE repo public.version_repositories%ROWTYPE; pin public.version_object_pins%ROWTYPE;
    item jsonb; oid text; key text; prefix text; suffix text; affected integer;
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
    IF jsonb_typeof(p_rows) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid_object_locations' USING ERRCODE='22023';
    END IF;
    IF jsonb_array_length(p_rows) NOT BETWEEN 1 AND 200 OR
       (SELECT count(DISTINCT value->>'object_id') FROM jsonb_array_elements(p_rows)) <> jsonb_array_length(p_rows) THEN
        RAISE EXCEPTION 'invalid_object_locations' USING ERRCODE='22023';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
        oid := item->>'object_id'; key := item->>'pack_key';
        IF item->>'project_id' IS DISTINCT FROM p_project_id
           OR public._version_oid_valid(oid, repo.object_format) IS DISTINCT FROM true
           OR jsonb_typeof(item->'offset_bytes') IS DISTINCT FROM 'number'
           OR jsonb_typeof(item->'size_bytes') IS DISTINCT FROM 'number'
           OR (item->>'offset_bytes')::bigint < 0 OR (item->>'size_bytes')::bigint <= 0 THEN
            RAISE EXCEPTION 'invalid_object_locations' USING ERRCODE='22023';
        END IF;
        IF left(key,8)='chunked:' THEN
            prefix := 'chunked:version/' || p_project_id || '/object-bundles/chunked/' ||
                      left(oid,2) || '/' || oid || '/manifest-';
            suffix := substring(key FROM length(prefix)+1);
            IF left(key,length(prefix)) IS DISTINCT FROM prefix OR suffix !~ '^[0-9a-f]{64}\.json$'
               OR (item->>'offset_bytes')::bigint <> 0 THEN
                RAISE EXCEPTION 'noncanonical_object_location' USING ERRCODE='22023';
            END IF;
        ELSE
            prefix := 'version/' || p_project_id || '/object-bundles/';
            suffix := substring(key FROM length(prefix)+1);
            IF left(key,length(prefix)) IS DISTINCT FROM prefix OR suffix !~ '^[0-9a-f]{2}/[0-9a-f]{64}\.(pob|pack)$'
               OR left(suffix,2) IS DISTINCT FROM substring(suffix FROM 4 FOR 2) THEN
                RAISE EXCEPTION 'noncanonical_object_location' USING ERRCODE='22023';
            END IF;
        END IF;
    END LOOP;
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
    RETURN jsonb_build_object('registered',affected);
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
        ELSE s.suffix !~ '^[0-9a-f]{2}/[0-9a-f]{64}\.(pob|pack)$'
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

NOTIFY pgrst,'reload schema';
COMMIT;
