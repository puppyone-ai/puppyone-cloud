-- Expand: bounded placement metadata without a repository-wide object ceiling.
BEGIN;
CREATE FUNCTION public.get_version_pinned_object_locations_page(
 p_project_id text,p_actor text,p_pin_id uuid,p_after text DEFAULT '',p_limit integer DEFAULT 4096
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result jsonb; has_more boolean;
BEGIN
 IF p_limit IS NULL OR p_limit<1 OR p_limit>4096 OR p_after IS NULL THEN
   RAISE EXCEPTION 'invalid_object_location_page';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.version_object_pins pin
   JOIN public.version_repositories repo USING(project_id)
   WHERE pin.id=p_pin_id AND pin.project_id=p_project_id AND pin.actor=p_actor
     AND pin.purpose='read' AND pin.state<>'released' AND pin.expires_at>statement_timestamp()
     AND pin.generation=repo.generation AND pin.gc_epoch=repo.gc_epoch) THEN
   RAISE EXCEPTION 'repository_read_pin_unavailable';
 END IF;
 SELECT coalesce(jsonb_agg(row ORDER BY object_id),'[]'::jsonb) INTO result FROM (
   SELECT object_id,pack_key,offset_bytes,size_bytes FROM public.version_object_locations
    WHERE project_id=p_project_id AND object_id>p_after ORDER BY object_id LIMIT p_limit+1
 ) row;
 has_more := jsonb_array_length(result)>p_limit;
 IF has_more THEN result := result-p_limit; END IF;
 RETURN jsonb_build_object('rows',result,'next_cursor',
   CASE WHEN has_more THEN result->-1->>'object_id' ELSE NULL END);
END $$;
REVOKE ALL ON FUNCTION public.get_version_pinned_object_locations_page(text,text,uuid,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.get_version_pinned_object_locations_page(text,text,uuid,text,integer) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
