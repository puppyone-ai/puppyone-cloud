-- Additive: every external Agent works in its own Workspace.
-- 1. One registered Agent has one Workspace and one branch, refs/heads/agents/<registration>.
--    The Workspace belongs to the person who approved it and starts from the Project's
--    branch, as an Automation does. It is created on first use and survives reconnects.
-- 2. A grant no longer reaches the owner's internal Workspace. Issuing an SSH ticket
--    moves the grant onto the Agent's Workspace, so grants made before this release
--    move the next time their Agent connects, with no data step.
-- 3. The owner's own SSH credentials do not use grants and keep the internal Workspace.
-- 4. A link admits one Agent, and a Project has at most 5 Agent seats.
-- Function signatures are unchanged, so callers from the previous release keep working.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.cloud_workbenches ADD COLUMN agent_registration_id uuid;
CREATE UNIQUE INDEX cloud_workbenches_agent ON public.cloud_workbenches(project_id,agent_registration_id)
 WHERE agent_registration_id IS NOT NULL AND owner_deleted_at IS NULL;

CREATE FUNCTION public.cloud_workbench_external_agent(p_user uuid,p_project text,p_registration uuid)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('cloud_workbench_agent_seats:'||p_project,0));
 SELECT * INTO w FROM public.cloud_workbenches WHERE project_id=p_project
   AND agent_registration_id=p_registration AND owner_deleted_at IS NULL;
 IF FOUND THEN RETURN w; END IF;
 IF (SELECT count(*) FROM public.cloud_workbenches WHERE project_id=p_project
   AND agent_registration_id IS NOT NULL AND owner_deleted_at IS NULL)>=5 THEN
   RAISE EXCEPTION 'workspace_agent_seat_limit' USING ERRCODE='53400';
 END IF;
 -- Saved work outlives a sleeping machine: it is reclaimed after seven days and
 -- rebuilt from the Agent's branch on the next connection.
 INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,agent_registration_id,retention_seconds)
 VALUES(p_user,p_project,'External Agent','refs/heads/agents/'||p_registration::text,p_registration,604800)
 RETURNING * INTO w;
 RETURN w;
END $$;

-- Saved work may be reclaimed. Only a pausing Workspace is final: nothing new can connect.
CREATE FUNCTION public.cloud_workbench_saved(p_workbench uuid,p_generation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 UPDATE public.cloud_workbenches SET unpublished_changes=false,updated_at=clock_timestamp()
 WHERE id=p_workbench AND generation=p_generation AND state='pausing'
   AND agent_registration_id IS NOT NULL RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

-- Many sleeping machines must not crowd out the running ones. A paused Workspace
-- needs maintenance only when it can be reclaimed or its owner was deleted.
CREATE OR REPLACE FUNCTION public.cloud_workbench_maintenance_in_pool(p_provider text,p_pool text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH due AS (SELECT id FROM public.cloud_workbenches WHERE state<>'retired' AND binding->>'provider'=p_provider
 AND worker_pool=p_pool
 AND (state<>'paused' OR owner_deleted_at IS NOT NULL OR (agent_registration_id IS NOT NULL
   AND NOT unpublished_changes AND paused_at+make_interval(secs=>retention_seconds)<=clock_timestamp()))
 AND maintenance_after<=clock_timestamp() ORDER BY maintenance_after FOR UPDATE SKIP LOCKED
 LIMIT least(greatest(p_limit,1),100))
 UPDATE public.cloud_workbenches w SET maintenance_after=clock_timestamp()+interval '30 seconds'
 FROM due WHERE w.id=due.id RETURNING w.*
$$;

-- The only place a grant learns which machine it reaches.
CREATE FUNCTION public.access_grant_agent_workspace(p_grant uuid)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE g public.access_grants; w public.cloud_workbenches;
BEGIN
 SELECT * INTO g FROM public.access_grants WHERE id=p_grant FOR UPDATE;
 IF NOT FOUND OR g.target_kind<>'workspace' THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 w:=public.cloud_workbench_external_agent(g.approved_by,g.project_id,g.registration_id);
 IF g.workbench_id IS DISTINCT FROM w.id THEN
   UPDATE public.access_grants SET workbench_id=w.id WHERE id=g.id;
 END IF;
 RETURN w;
END $$;

-- Which machine an Agent reaches, before a ticket can be bound to its generation.
CREATE FUNCTION public.access_agent_workspace(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections; g public.access_grants;
BEGIN
 SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
 IF NOT FOUND OR c.grant_id IS NULL THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 PERFORM public.access_grant_context(c.grant_id,'ssh');
 SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
 IF NOT('workspace.shell'=ANY(g.capabilities)) THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 RETURN to_jsonb(public.access_grant_agent_workspace(g.id));
END $$;

-- The same answer for a registered client, proved by its renewal key.
CREATE FUNCTION public.access_registered_workspace(p_key_hashes text[],p_grant uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE k public.access_registration_credentials; g public.access_grants;
BEGIN
 SELECT * INTO k FROM public.access_registration_credentials
 WHERE key_hash=ANY(p_key_hashes) AND state='active' AND expires_at>clock_timestamp();
 SELECT * INTO g FROM public.access_grants WHERE id=p_grant AND registration_id=k.registration_id;
 IF k.id IS NULL OR g.id IS NULL OR NOT('workspace.wake'=ANY(g.capabilities)) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 PERFORM public.access_grant_context(g.id,'ssh');
 RETURN to_jsonb(public.access_grant_agent_workspace(g.id));
END $$;

CREATE OR REPLACE FUNCTION public.access_assert_owner(p_user uuid,p_project text,p_workbench uuid DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority record;
BEGIN
 SELECT * INTO authority FROM public.resolve_project_role(p_project,p_user);
 IF NOT FOUND OR authority.effective_role<>'admin' OR NOT EXISTS(
   SELECT 1 FROM public.projects WHERE id=p_project AND org_id=authority.org_id AND lifecycle_status='ready') THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 IF p_workbench IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.cloud_workbenches
   WHERE id=p_workbench AND project_id=p_project AND user_id=p_user
     AND (is_default OR agent_registration_id IS NOT NULL) AND owner_deleted_at IS NULL) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 RETURN authority.org_id;
END $$;

CREATE OR REPLACE FUNCTION public.access_issue_credential(p_key_hashes text[],p_next_key_hash text,p_request uuid,
 p_grant uuid,p_channel text,p_credential_hash text DEFAULT NULL,p_public_key text DEFAULT NULL,
 p_surface text DEFAULT NULL,p_ttl integer DEFAULT 900)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE k public.access_registration_credentials; r public.access_registrations; g public.access_grants;
 s public.access_surfaces; w public.cloud_workbenches; context jsonb; result jsonb; expiry timestamptz;
 fingerprint jsonb:=jsonb_build_object('credential_hash',p_credential_hash,'public_key',p_public_key,'surface',p_surface,'ttl',p_ttl);
BEGIN
 SELECT * INTO k FROM public.access_registration_credentials WHERE key_hash=ANY(p_key_hashes);
 IF NOT FOUND THEN RETURN jsonb_build_object('error','access_invalid_credential'); END IF;
 SELECT * INTO r FROM public.access_registrations WHERE id=k.registration_id FOR UPDATE;
 SELECT * INTO k FROM public.access_registration_credentials WHERE id=k.id FOR UPDATE;
 IF r.state<>'active' OR k.state='revoked' OR k.expires_at<=clock_timestamp() THEN
   RETURN jsonb_build_object('error','access_invalid_credential'); END IF;
 IF k.state='rotated' THEN
   IF k.request_id=p_request AND k.next_key_hash=p_next_key_hash
      AND k.receipt->>'grant_id'=p_grant::text AND k.receipt->>'channel'=p_channel
      AND k.receipt->'_request'=fingerprint THEN
     PERFORM public.access_grant_context(p_grant,p_channel);
     RETURN k.receipt-'_request';
   END IF;
   UPDATE public.access_registrations SET state='suspended',revision=revision+1,updated_at=clock_timestamp() WHERE id=r.id;
   PERFORM public.access_revoke_registration_credentials(r.id);
   UPDATE public.access_sessions SET state='stopping' WHERE registration_id=r.id AND state='active';
   INSERT INTO public.access_lifecycle_events(registration_id,event) VALUES(r.id,'registration.credential_reused');
   RETURN jsonb_build_object('error','access_credential_reused');
 END IF;
 IF p_next_key_hash IS NULL OR length(p_next_key_hash)<>64 OR p_next_key_hash=k.key_hash
   OR p_request IS NULL OR p_ttl NOT BETWEEN 60 AND 3600 THEN
   RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
 SELECT * INTO g FROM public.access_grants WHERE id=p_grant FOR UPDATE;
 IF NOT FOUND OR g.registration_id<>r.id THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 context:=public.access_grant_context(g.id,p_channel);
 expiry:=least(clock_timestamp()+make_interval(secs=>p_ttl),g.expires_at);
 IF p_channel='ssh' THEN
   IF g.target_kind<>'workspace' OR NOT('workspace.shell'=ANY(g.capabilities))
     OR p_public_key IS NULL OR p_public_key NOT LIKE 'ssh-ed25519 %' THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   w:=public.access_grant_agent_workspace(g.id);
   -- A paused workspace keeps its generation; resuming it is a separately approved capability.
   IF w.owner_deleted_at IS NOT NULL OR NOT (w.state='running' OR ('workspace.wake'=ANY(g.capabilities)
     AND w.state IN ('paused','pausing','resuming'))) THEN
     RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000'; END IF;
   INSERT INTO public.workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at,access_grant_id)
   VALUES(p_request,w.id,w.generation,w.user_id,w.project_id,p_public_key,expiry,g.id);
   result:=jsonb_build_object('id',p_request,'grant_id',g.id,'registration_id',r.id,'channel',p_channel,
     'workbench_id',w.id,'generation',w.generation,'expires_at',expiry);
 ELSE
   IF g.target_kind<>'project_root' OR p_credential_hash IS NULL OR length(p_credential_hash)<>64 THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   SELECT * INTO s FROM public.access_surfaces WHERE id=p_surface AND project_id=g.project_id AND org_id=g.org_id
     AND scope_id IS NULL AND status='active' AND kind=CASE p_channel WHEN 'mcp' THEN 'mcp' ELSE 'git_remote' END;
   IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   INSERT INTO public.access_surface_credentials(id,org_id,project_id,access_surface_id,credential_type,
     key_prefix,key_last4,key_hash,expires_at,created_by,grant_mode,credential_lifecycle,access_grant_id,access_channel)
   VALUES(p_request::text,g.org_id,g.project_id,s.id,CASE p_channel WHEN 'mcp' THEN 'bearer_token' ELSE 'git_http_token' END,
     CASE p_channel WHEN 'mcp' THEN 'mcp' ELSE 'git' END,'****',p_credential_hash,expiry,g.approved_by,
     CASE WHEN 'repository.write'=ANY(g.capabilities) THEN 'rw' ELSE 'r' END,'session',g.id,p_channel);
   result:=jsonb_build_object('id',p_request,'grant_id',g.id,'registration_id',r.id,'channel',p_channel,
     'project_id',g.project_id,'access_surface_id',s.id,'expires_at',expiry);
 END IF;
 UPDATE public.access_registration_credentials SET state='rotated',request_id=p_request,next_key_hash=p_next_key_hash,receipt=result||jsonb_build_object('_request',fingerprint)
 WHERE id=k.id;
 INSERT INTO public.access_registration_credentials(id,registration_id,key_hash,expires_at)
 VALUES(gen_random_uuid(),r.id,p_next_key_hash,clock_timestamp()+interval '90 days');
 INSERT INTO public.access_lifecycle_events(registration_id,grant_id,event) VALUES(r.id,g.id,'credential.issued');
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_acquire(p_id uuid,p_workbench uuid,p_generation uuid,
 p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; c public.cloud_workbench_connections;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 -- Connections record the workspace owner. Serialize one owner's admissions so
 -- concurrent connections cannot pass the bounds together.
 PERFORM pg_advisory_xact_lock(hashtextextended('cloud_workbench_connection_user:'||p_user::text,0));
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.user_id IS DISTINCT FROM p_user
   OR w.project_id IS DISTINCT FROM p_project OR NOT (w.is_default OR w.agent_registration_id IS NOT NULL) OR w.owner_deleted_at IS NOT NULL
   OR w.state<>'running' OR w.resource IS NULL OR coalesce(w.resource->>'allocation_pending','true')<>'false' THEN
   RAISE EXCEPTION 'workspace_connection_denied' USING ERRCODE='42501';
 END IF;
 IF p_id IS NULL OR EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE id=p_id) THEN
   RAISE EXCEPTION 'workspace_connection_already_used' USING ERRCODE='42501';
 END IF;
 -- Occupancy is counted until cleanup is confirmed, so a leaked session still counts.
 IF (SELECT count(*) FROM public.cloud_workbench_connections WHERE workbench_id=w.id AND state<>'released')>=10 THEN
   RAISE EXCEPTION 'workspace_connection_limit' USING ERRCODE='53400', DETAIL='workspace';
 END IF;
 -- Every external Agent has its own Workspace, so only the owner's own Workspaces share this bound.
 IF w.agent_registration_id IS NULL AND (SELECT count(*) FROM public.cloud_workbench_connections x
   JOIN public.cloud_workbenches o ON o.id=x.workbench_id
   WHERE x.user_id=p_user AND x.state<>'released' AND o.agent_registration_id IS NULL)>=20 THEN
   RAISE EXCEPTION 'workspace_connection_limit' USING ERRCODE='53400', DETAIL='owner';
 END IF;
 IF w.next_task_uid>59999 THEN RAISE EXCEPTION 'workbench_task_identity_exhausted'; END IF;
 INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch)
 VALUES(p_id,w.id,w.generation,p_user,p_project,w.next_task_uid,
   w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',w.next_task_uid),w.binding,w.compute_epoch) RETURNING * INTO c;
 UPDATE public.cloud_workbenches SET next_task_uid=next_task_uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
 RETURN to_jsonb(c);
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
     'connections',coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'admission',l.admission,
       'grant_id',x.grant_id,'connected_at',x.decided_at,'expires_at',parent.expires_at,
       'branch','refs/heads/agents/'||parent.registration_id::text) ORDER BY x.decided_at DESC)
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

CREATE OR REPLACE FUNCTION public.access_agent_ssh_ticket(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections; g public.access_grants; w public.cloud_workbenches; ticket uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
 IF NOT FOUND OR c.grant_id IS NULL THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 PERFORM public.access_grant_context(c.grant_id,'ssh');
 SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
 IF g.target_kind<>'workspace' OR NOT('workspace.shell'=ANY(g.capabilities)) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 w:=public.access_grant_agent_workspace(g.id);
 IF w.owner_deleted_at IS NOT NULL OR NOT (w.state='running' OR ('workspace.wake'=ANY(g.capabilities)
   AND w.state IN ('paused','pausing','resuming'))) THEN
   RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO public.workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at,access_grant_id)
 VALUES(ticket,w.id,w.generation,w.user_id,w.project_id,c.public_key,
   least(clock_timestamp()+interval '2 minutes',g.expires_at),g.id);
 INSERT INTO public.access_lifecycle_events(registration_id,grant_id,event) VALUES(g.registration_id,g.id,'credential.issued');
 RETURN jsonb_build_object('id',ticket);
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_saved(uuid,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_saved(uuid,uuid) TO service_role;
REVOKE ALL ON FUNCTION public.cloud_workbench_external_agent(uuid,text,uuid),
 public.access_grant_agent_workspace(uuid),public.access_agent_workspace(uuid),
 public.access_registered_workspace(text[],uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_agent_workspace(uuid),
 public.access_registered_workspace(text[],uuid) TO service_role;
COMMIT;
