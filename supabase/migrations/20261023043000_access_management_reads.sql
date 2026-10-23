BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

-- Human control-plane inspection only. No credential hashes or renewal receipts.
CREATE FUNCTION public.access_inspect(p_user uuid,p_collection text,
 p_project text DEFAULT NULL,p_registration uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 IF p_user IS NULL THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_registration IS NOT NULL THEN
   IF p_project IS NOT NULL OR p_collection<>'requests' OR NOT EXISTS(
     SELECT 1 FROM public.access_registrations WHERE id=p_registration AND owner_user_id=p_user
   ) THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 ELSE
   PERFORM public.access_assert_owner(p_user,p_project);
 END IF;
 IF p_collection='requests' THEN
   SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC,x.id) INTO result FROM (
     SELECT r.*,a.name AS registration_name,i.project_id,i.target_kind,i.workbench_id,
       i.capabilities,i.channels,i.max_grant_seconds,g.id AS grant_id
     FROM public.access_requests r
     JOIN public.access_registrations a ON a.id=r.registration_id
     JOIN public.access_invitations i ON i.id=r.invitation_id
     LEFT JOIN public.access_grants g ON g.request_id=r.id
     WHERE CASE WHEN p_registration IS NOT NULL THEN r.registration_id=p_registration
       ELSE i.project_id=p_project END
     ORDER BY r.created_at DESC,r.id LIMIT 200
   ) x;
 ELSIF p_collection='grants' THEN
   SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC,x.id) INTO result FROM (
     SELECT g.*,a.name AS registration_name FROM public.access_grants g
     JOIN public.access_registrations a ON a.id=g.registration_id
     WHERE g.project_id=p_project ORDER BY g.created_at DESC,g.id LIMIT 200
   ) x;
 ELSIF p_collection='sessions' THEN
   SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at DESC,x.id) INTO result FROM (
     SELECT s.*,a.name AS registration_name FROM public.access_sessions s
     JOIN public.access_registrations a ON a.id=s.registration_id
     JOIN public.access_grants g ON g.id=s.grant_id
     WHERE g.project_id=p_project ORDER BY s.created_at DESC,s.id LIMIT 200
   ) x;
 ELSIF p_collection='invitations' THEN
   RETURN public.access_grant_command('invitations',p_user,jsonb_build_object('project_id',p_project));
 ELSE RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
 RETURN coalesce(result,'[]'::jsonb);
END $$;
REVOKE ALL ON FUNCTION public.access_inspect(uuid,text,text,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_inspect(uuid,text,text,uuid) TO service_role;
COMMIT;
