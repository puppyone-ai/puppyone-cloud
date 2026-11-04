-- Additive: an Agent chooses the picture shown beside its name when it connects.
-- 1. access_agent_connections.avatar holds that choice, one of a short list the
--    API keeps. NULL is no choice: every connection made before this, and any
--    Agent that chose nothing the list holds.
-- 2. access_agent_connect gains a five-argument form that records it. The
--    four-argument form keeps its signature and records no choice through the
--    new one, so callers from the previous release keep working.
-- 3. The owner's inventory, pending requests and review carry it beside the name.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.access_agent_connections ADD COLUMN avatar text
 CHECK(avatar IS NULL OR avatar ~ '^[a-z0-9-]{1,40}$');

CREATE FUNCTION public.access_agent_connect(p_key_hashes text[],p_poll_hash text,p_agent_name text,p_public_key text,
 p_agent_avatar text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE i public.access_invitations; c public.access_agent_connections;
BEGIN
 SELECT * INTO i FROM public.access_invitations WHERE key_hash=ANY(p_key_hashes) FOR UPDATE;
 IF NOT FOUND OR i.admission='approval' OR i.state<>'active' OR i.expires_at<=clock_timestamp()
   OR i.approvals>=i.max_approvals THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 PERFORM public.access_assert_owner(i.created_by,i.project_id,i.workbench_id);
 -- A leaked link cannot flood its owner with sign-in requests.
 IF (SELECT count(*) FROM public.access_agent_connections WHERE invitation_id=i.id AND state='pending'
   AND expires_at>clock_timestamp())>=20 THEN
   RAISE EXCEPTION 'access_conflict' USING ERRCODE='55000'; END IF;
 INSERT INTO public.access_agent_connections(id,invitation_id,poll_hash,name,avatar,public_key,expires_at)
 VALUES(gen_random_uuid(),i.id,p_poll_hash,p_agent_name,p_agent_avatar,p_public_key,clock_timestamp()+interval '10 minutes')
 RETURNING * INTO c;
 IF i.admission='link' THEN c:=public.access_agent_admit(c,i,i.created_by); END IF;
 RETURN jsonb_build_object('id',c.id,'state',c.state,'expires_at',c.expires_at);
END $$;

CREATE OR REPLACE FUNCTION public.access_agent_connect(p_key_hashes text[],p_poll_hash text,p_agent_name text,p_public_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 RETURN public.access_agent_connect(p_key_hashes,p_poll_hash,p_agent_name,p_public_key,NULL::text);
END $$;

CREATE OR REPLACE FUNCTION public.access_agent_link_command(p_action text,p_user uuid,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE i public.access_invitations; c public.access_agent_connections; g public.access_grants;
 project text:=p_payload->>'project_id'; identity uuid:=(p_payload->>'id')::uuid;
 workbench uuid:=(p_payload->>'workbench_id')::uuid; mode text:=p_payload->>'admission';
 key_hash text:=p_payload->>'key_hash'; decision text:=p_payload->>'decision'; organization text;
BEGIN
 IF p_user IS NULL OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_action='create' THEN
   IF workbench IS NULL OR mode IS NULL OR mode NOT IN ('sign_in','link') OR key_hash IS NULL OR length(key_hash)<>64 THEN
     RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
   organization:=public.access_assert_owner(p_user,project,workbench);
   -- The policy is the server's: a client cannot widen what a link grants.
   INSERT INTO public.access_invitations(id,key_hash,project_id,org_id,created_by,target_kind,workbench_id,
     capabilities,channels,max_grant_seconds,expires_at,max_approvals,admission)
   VALUES(identity,key_hash,project,organization,p_user,'workspace',workbench,
     ARRAY['workspace.read','workspace.write','workspace.shell','workspace.wake'],ARRAY['ssh'],
     CASE mode WHEN 'link' THEN 604800 ELSE 2592000 END,clock_timestamp()+interval '7 days',1,mode)
   ON CONFLICT(id) DO NOTHING;
   SELECT * INTO i FROM public.access_invitations WHERE id=identity;
   IF i.created_by IS DISTINCT FROM p_user OR i.project_id IS DISTINCT FROM project OR i.key_hash IS DISTINCT FROM key_hash
     OR i.workbench_id IS DISTINCT FROM workbench OR i.admission IS DISTINCT FROM mode THEN
     RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501'; END IF;
   RETURN to_jsonb(i)-'key_hash';
 ELSIF p_action='inventory' THEN
   PERFORM public.access_assert_owner(p_user,project);
   RETURN jsonb_build_object(
     'links',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'admission',x.admission,'expires_at',x.expires_at,
       'created_at',x.created_at) ORDER BY x.created_at DESC) FROM public.access_invitations x
       WHERE x.project_id=project AND x.created_by=p_user AND x.admission<>'approval'
         AND x.state='active' AND x.expires_at>clock_timestamp()),'[]'),
     'connections',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'avatar',x.avatar,'admission',l.admission,
       'grant_id',x.grant_id,'connected_at',x.decided_at,'expires_at',parent.expires_at,
       'branch','refs/heads/agents/'||parent.registration_id::text) ORDER BY x.decided_at DESC)
       FROM public.access_agent_connections x
       JOIN public.access_invitations l ON l.id=x.invitation_id
       JOIN public.access_grants parent ON parent.id=x.grant_id
       WHERE l.project_id=project AND l.created_by=p_user
         AND public.access_agent_connection_state(x)='approved'),'[]'),
     -- Exactly the requests 'decide' would still approve.
     'pending',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'avatar',x.avatar,'admission',l.admission,
       'requested_at',x.created_at,'expires_at',x.expires_at) ORDER BY x.created_at DESC)
       FROM public.access_agent_connections x
       JOIN public.access_invitations l ON l.id=x.invitation_id
       WHERE l.project_id=project AND l.created_by=p_user AND l.admission<>'approval'
         AND x.state='pending' AND x.expires_at>clock_timestamp()
         AND l.state='active' AND l.expires_at>clock_timestamp() AND l.approvals<l.max_approvals),'[]'));
 ELSIF p_action='stop' THEN
   PERFORM public.access_assert_owner(p_user,project);
   UPDATE public.access_invitations SET state='revoked'
   WHERE project_id=project AND created_by=p_user AND admission<>'approval' AND state='active';
   FOR g IN SELECT parent.* FROM public.access_grants parent
     JOIN public.access_agent_connections x ON x.grant_id=parent.id
     JOIN public.access_invitations l ON l.id=x.invitation_id
     WHERE l.project_id=project AND l.created_by=p_user AND parent.state<>'revoked' ORDER BY parent.id LOOP
     PERFORM public.access_grant_command('grant_state',p_user,
       jsonb_build_object('id',g.id,'project_id',project,'state','revoked'));
   END LOOP;
   RETURN jsonb_build_object('stopped',true);
 ELSIF p_action NOT IN ('review','decide') THEN
   RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM public.access_agent_connections WHERE id=identity;
 IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_action='decide' THEN
   SELECT * INTO i FROM public.access_invitations WHERE id=c.invitation_id FOR UPDATE;
   SELECT * INTO c FROM public.access_agent_connections WHERE id=identity FOR UPDATE;
 ELSE
   SELECT * INTO i FROM public.access_invitations WHERE id=c.invitation_id;
 END IF;
 PERFORM public.access_assert_owner(p_user,i.project_id,i.workbench_id);
 IF p_action='decide' THEN
   IF decision IS NULL OR decision NOT IN ('approve','reject') THEN
     RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
   -- Repeating a decision is not a new one.
   IF (decision='approve' AND c.state='approved') OR (decision='reject' AND c.state='rejected') THEN NULL;
   ELSIF c.state<>'pending' OR c.expires_at<=clock_timestamp() OR i.state<>'active'
     OR i.expires_at<=clock_timestamp() OR i.approvals>=i.max_approvals THEN
     RAISE EXCEPTION 'access_conflict' USING ERRCODE='55000';
   ELSIF decision='approve' THEN c:=public.access_agent_admit(c,i,p_user);
   ELSE
     UPDATE public.access_agent_connections SET state='rejected',decided_by=p_user,decided_at=clock_timestamp()
     WHERE id=c.id RETURNING * INTO c;
   END IF;
 END IF;
 RETURN jsonb_build_object('id',c.id,'name',c.name,'avatar',c.avatar,'state',public.access_agent_connection_state(c),
   'project_id',i.project_id,'project_name',(SELECT name FROM public.projects WHERE id=i.project_id),
   'created_at',c.created_at,'expires_at',c.expires_at,'grant_seconds',i.max_grant_seconds);
END $$;

REVOKE ALL ON FUNCTION public.access_agent_connect(text[],text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_agent_connect(text[],text,text,text,text) TO service_role;
COMMIT;
