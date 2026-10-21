-- Expand only: bounded network pages, with all existing checked SQL operations
-- retained. Each page is atomic, including its internal 200-row chunks. This
-- removes network round trips, not per-object database work. Old RPCs remain
-- available to old binaries; deploy this migration before the new writer.
BEGIN;

CREATE FUNCTION public._version_metadata_chunks(p_rows jsonb)
RETURNS SETOF jsonb LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
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
 RETURN QUERY SELECT jsonb_agg(value ORDER BY ordinal)
   FROM jsonb_array_elements(p_rows) WITH ORDINALITY AS r(value,ordinal)
   GROUP BY (ordinal-1)/200 ORDER BY (ordinal-1)/200;
END $$;

CREATE FUNCTION public.reserve_version_object_capacity_page(
 p_project_id text,p_actor text,p_pin_id uuid,p_objects jsonb,
 p_required boolean DEFAULT true,p_io_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE chunk jsonb; result jsonb; objects bigint:=0; bytes bigint:=0;
BEGIN
 FOR chunk IN SELECT public._version_metadata_chunks(p_objects) LOOP
   result:=public.reserve_version_object_capacity(
     p_project_id,p_actor,p_pin_id,chunk,p_required,p_io_id);
   objects:=objects+(result->>'new_objects')::bigint;
   bytes:=bytes+(result->>'new_body_bytes')::bigint;
 END LOOP;
 RETURN jsonb_build_object('new_objects',objects,'new_body_bytes',bytes)
   || CASE WHEN result->>'profile'='dormant' THEN jsonb_build_object('profile','dormant') ELSE '{}'::jsonb END;
END $$;

CREATE FUNCTION public.register_version_object_location_page(
 p_project_id text,p_actor text,p_pin_id uuid,p_rows jsonb)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE chunk jsonb; result jsonb; registered bigint:=0;
BEGIN
 FOR chunk IN SELECT public._version_metadata_chunks(p_rows) LOOP
   result:=public.register_version_object_locations(p_project_id,p_actor,p_pin_id,chunk);
   registered:=registered+(result->>'registered')::bigint;
 END LOOP;
 RETURN jsonb_build_object('registered',registered);
END $$;

CREATE FUNCTION public.register_version_object_proof_page(
 p_project_id text,p_actor text,p_pin_id uuid,p_rows jsonb)
RETURNS integer LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE chunk jsonb; registered integer:=0;
BEGIN
 -- Keep dependency order across internal chunks. They share one transaction,
 -- so a missing dependency in the last chunk rolls back the entire page.
 FOR chunk IN SELECT public._version_metadata_chunks(p_rows) LOOP
   registered:=registered+public.register_version_object_proofs(p_project_id,p_actor,p_pin_id,chunk);
 END LOOP;
 RETURN registered;
END $$;

CREATE FUNCTION public.seal_version_capacity_page(
 p_project_id text,p_actor text,p_pin_id uuid,p_objects jsonb,
 p_manifest_sha256 text,p_root_details jsonb)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_objects IS DISTINCT FROM '[]'::jsonb THEN
   PERFORM public.reserve_version_object_capacity_page(p_project_id,p_actor,p_pin_id,p_objects,true,NULL);
 END IF;
 RETURN public.seal_capacity_version_object_publication(
   p_project_id,p_actor,p_pin_id,p_manifest_sha256,p_root_details);
END $$;

REVOKE ALL ON FUNCTION public._version_metadata_chunks(jsonb),
 public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid),
 public.register_version_object_location_page(text,text,uuid,jsonb),
 public.register_version_object_proof_page(text,text,uuid,jsonb),
 public.seal_version_capacity_page(text,text,uuid,jsonb,text,jsonb)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public._version_metadata_chunks(jsonb),
 public.reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid),
 public.register_version_object_location_page(text,text,uuid,jsonb),
 public.register_version_object_proof_page(text,text,uuid,jsonb),
 public.seal_version_capacity_page(text,text,uuid,jsonb,text,jsonb)
 TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
