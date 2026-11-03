-- Additive: hand a Hosted Workspace to an Agent with one link.
-- 1. An invitation may admit its Agent after the owner signs in once ('sign_in'),
--    or from the link alone ('link'). 'approval' keeps the existing reviewed flow.
-- 2. An Agent needs no account, CLI or renewal key: it keeps one SSH key, which is
--    the durable proof, and the gateway mints the one-use ticket for each connection.
-- Grants, sessions, revocation and audit stay the existing lifecycle records.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.access_invitations ADD COLUMN admission text NOT NULL DEFAULT 'approval'
 CHECK(admission IN ('approval','sign_in','link'));
ALTER TABLE public.access_invitations ADD CONSTRAINT access_invitation_agent_link_shape CHECK
 (admission='approval' OR (target_kind='workspace' AND channels=ARRAY['ssh'] AND max_grant_seconds IS NOT NULL));

CREATE TABLE public.access_agent_connections (
 id uuid PRIMARY KEY,
 invitation_id uuid NOT NULL REFERENCES public.access_invitations(id) ON DELETE CASCADE,
 poll_hash text NOT NULL UNIQUE CHECK(length(poll_hash)=64),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 public_key text NOT NULL CHECK(public_key LIKE 'ssh-ed25519 %' AND length(public_key) BETWEEN 40 AND 8192),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected')),
 grant_id uuid UNIQUE REFERENCES public.access_grants(id) ON DELETE SET NULL,
 decided_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 decided_at timestamptz,
 expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(expires_at>created_at),
 CHECK((state='pending')=(decided_at IS NULL))
);
CREATE INDEX access_agent_connections_invitation ON public.access_agent_connections(invitation_id,state,expires_at);
ALTER TABLE public.access_agent_connections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.access_agent_connections FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.access_agent_connections TO service_role;

-- The Agent has no account: the delegating human owns its registration, so every
-- existing check of approver, tenant and owner applies to it unchanged.
CREATE FUNCTION public.access_agent_admit(c public.access_agent_connections,i public.access_invitations,p_approver uuid)
RETURNS public.access_agent_connections LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE registration uuid:=gen_random_uuid(); request uuid:=gen_random_uuid(); g public.access_grants; expiry timestamptz;
BEGIN
 expiry:=clock_timestamp()+make_interval(secs=>i.max_grant_seconds);
 -- Access from the link alone never outlives the link.
 IF i.admission='link' THEN expiry:=least(expiry,i.expires_at); END IF;
 INSERT INTO public.access_registrations(id,owner_user_id,name) VALUES(registration,p_approver,c.name);
 INSERT INTO public.access_requests(id,invitation_id,registration_id,applicant_user_id,state,decided_by,decided_at)
 VALUES(request,i.id,registration,p_approver,'approved',p_approver,clock_timestamp());
 INSERT INTO public.access_grants(id,request_id,registration_id,project_id,org_id,approved_by,
   target_kind,workbench_id,capabilities,channels,expires_at)
 VALUES(gen_random_uuid(),request,registration,i.project_id,i.org_id,p_approver,
   i.target_kind,i.workbench_id,i.capabilities,i.channels,expiry) RETURNING * INTO g;
 UPDATE public.access_invitations SET approvals=approvals+1 WHERE id=i.id;
 UPDATE public.access_agent_connections SET state='approved',grant_id=g.id,decided_by=p_approver,decided_at=clock_timestamp()
 WHERE id=c.id RETURNING * INTO c;
 INSERT INTO public.access_lifecycle_events(registration_id,grant_id,actor_user_id,event)
 VALUES(registration,g.id,p_approver,'agent_link.'||i.admission);
 RETURN c;
END $$;

-- What a connection amounts to now, whatever happened to it since it was approved.
CREATE FUNCTION public.access_agent_connection_state(c public.access_agent_connections)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT CASE
  WHEN c.state='pending' THEN CASE WHEN c.expires_at<=clock_timestamp() THEN 'expired' ELSE 'pending' END
  WHEN c.state='rejected' THEN 'rejected'
  WHEN EXISTS(SELECT 1 FROM public.access_grants g JOIN public.access_registrations r ON r.id=g.registration_id
    WHERE g.id=c.grant_id AND g.state='active' AND r.state='active'
      AND (g.expires_at IS NULL OR g.expires_at>clock_timestamp())) THEN 'approved'
  ELSE 'revoked' END
$$;

-- Human control plane. Only the Workspace's owner creates, reviews or ends these.
CREATE FUNCTION public.access_agent_link_command(p_action text,p_user uuid,p_payload jsonb)
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
     CASE mode WHEN 'link' THEN 604800 ELSE 2592000 END,clock_timestamp()+interval '7 days',100,mode)
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
     'connections',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'admission',l.admission,
       'grant_id',x.grant_id,'connected_at',x.decided_at,'expires_at',parent.expires_at) ORDER BY x.decided_at DESC)
       FROM public.access_agent_connections x
       JOIN public.access_invitations l ON l.id=x.invitation_id
       JOIN public.access_grants parent ON parent.id=x.grant_id
       WHERE l.project_id=project AND l.created_by=p_user
         AND public.access_agent_connection_state(x)='approved'),'[]'));
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
 RETURN jsonb_build_object('id',c.id,'name',c.name,'state',public.access_agent_connection_state(c),
   'project_id',i.project_id,'project_name',(SELECT name FROM public.projects WHERE id=i.project_id),
   'created_at',c.created_at,'expires_at',c.expires_at,'grant_seconds',i.max_grant_seconds);
END $$;

-- An Agent applies with the link alone. Nothing here is a credential yet: a
-- 'sign_in' link waits for the owner, and only a 'link' invitation admits at once.
CREATE FUNCTION public.access_agent_connect(p_key_hashes text[],p_poll_hash text,p_agent_name text,p_public_key text)
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
 INSERT INTO public.access_agent_connections(id,invitation_id,poll_hash,name,public_key,expires_at)
 VALUES(gen_random_uuid(),i.id,p_poll_hash,p_agent_name,p_public_key,clock_timestamp()+interval '10 minutes')
 RETURNING * INTO c;
 IF i.admission='link' THEN c:=public.access_agent_admit(c,i,i.created_by); END IF;
 RETURN jsonb_build_object('id',c.id,'state',c.state,'expires_at',c.expires_at);
END $$;

CREATE FUNCTION public.access_agent_connection_status(p_id uuid,p_poll_hashes text[])
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections;
BEGIN
 SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND poll_hash=ANY(p_poll_hashes);
 IF NOT FOUND THEN RETURN jsonb_build_object('error','access_invalid_credential'); END IF;
 RETURN jsonb_build_object('id',c.id,'state',public.access_agent_connection_state(c),
   'expires_at',(SELECT g.expires_at FROM public.access_grants g WHERE g.id=c.grant_id));
END $$;

-- Whether this key may sign in now. A probe writes nothing and mints nothing.
CREATE FUNCTION public.access_agent_ssh_admit(p_id uuid,p_public_key text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections;
BEGIN
 SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved' AND public_key=p_public_key;
 IF NOT FOUND OR c.grant_id IS NULL THEN RETURN false; END IF;
 PERFORM public.access_grant_context(c.grant_id,'ssh');
 RETURN true;
EXCEPTION WHEN insufficient_privilege THEN RETURN false;
END $$;

-- One authenticated SSH connection becomes one existing one-use ticket, bound to
-- the workspace's current generation, so revocation, session leases and occupancy
-- are enforced by the same path as every other managed SSH connection.
CREATE FUNCTION public.access_agent_ssh_ticket(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections; g public.access_grants; w public.cloud_workbenches; ticket uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
 IF NOT FOUND OR c.grant_id IS NULL THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 PERFORM public.access_grant_context(c.grant_id,'ssh');
 SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
 IF g.target_kind<>'workspace' OR NOT('workspace.shell'=ANY(g.capabilities)) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=g.workbench_id FOR SHARE;
 IF w.owner_deleted_at IS NOT NULL OR NOT (w.state='running' OR ('workspace.wake'=ANY(g.capabilities)
   AND w.state IN ('paused','pausing','resuming'))) THEN
   RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO public.workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at,access_grant_id)
 VALUES(ticket,w.id,w.generation,w.user_id,w.project_id,c.public_key,
   least(clock_timestamp()+interval '2 minutes',g.expires_at),g.id);
 INSERT INTO public.access_lifecycle_events(registration_id,grant_id,event) VALUES(g.registration_id,g.id,'credential.issued');
 RETURN jsonb_build_object('id',ticket);
END $$;

REVOKE ALL ON FUNCTION public.access_agent_admit(public.access_agent_connections,public.access_invitations,uuid),
 public.access_agent_connection_state(public.access_agent_connections),
 public.access_agent_link_command(text,uuid,jsonb),public.access_agent_connect(text[],text,text,text),
 public.access_agent_connection_status(uuid,text[]),public.access_agent_ssh_admit(uuid,text),
 public.access_agent_ssh_ticket(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_agent_link_command(text,uuid,jsonb),public.access_agent_connect(text[],text,text,text),
 public.access_agent_connection_status(uuid,text[]),public.access_agent_ssh_admit(uuid,text),
 public.access_agent_ssh_ticket(uuid) TO service_role;
COMMIT;
