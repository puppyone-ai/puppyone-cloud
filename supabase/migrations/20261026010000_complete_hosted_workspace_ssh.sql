-- Additive: complete Hosted Workspace SSH.
-- 1. 'workspace.wake' lets an owner approve a client to resume a paused workspace.
-- 2. Lifecycle SSH credentials may be issued for a paused workspace with that capability.
-- 3. External connections are bounded per workspace (10) and per workspace owner (20).
-- Function signatures are unchanged, so callers from the previous release keep working.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE OR REPLACE FUNCTION public.access_authority_shape(p_target text,p_capabilities text[],p_channels text[])
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT p_capabilities IS NOT NULL AND p_channels IS NOT NULL
 AND cardinality(p_capabilities)>0 AND cardinality(p_channels)>0
 AND array_position(p_capabilities,NULL) IS NULL AND array_position(p_channels,NULL) IS NULL
 AND CASE p_target
 WHEN 'project_root' THEN p_capabilities <@ ARRAY['repository.read','repository.write']
   AND 'repository.read'=ANY(p_capabilities) AND p_channels <@ ARRAY['git','cli','mcp']
 WHEN 'workspace' THEN p_capabilities <@ ARRAY['workspace.read','workspace.write','workspace.shell','workspace.wake']
   AND 'workspace.read'=ANY(p_capabilities) AND p_channels <@ ARRAY['ssh','mcp']
   AND (NOT ('workspace.shell'=ANY(p_capabilities)) OR 'workspace.write'=ANY(p_capabilities))
   AND (NOT ('ssh'=ANY(p_channels)) OR 'workspace.shell'=ANY(p_capabilities))
 ELSE false END
$$;

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
   SELECT * INTO w FROM public.cloud_workbenches WHERE id=g.workbench_id FOR SHARE;
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

REVOKE ALL ON FUNCTION public.access_registration_command(text,uuid,jsonb),public.access_grant_command(text,uuid,jsonb),
 public.access_issue_credential(text[],text,uuid,uuid,text,text,text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_registration_command(text,uuid,jsonb),public.access_grant_command(text,uuid,jsonb),
 public.access_issue_credential(text[],text,uuid,uuid,text,text,text,text,integer) TO service_role;
COMMIT;


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
   OR w.project_id IS DISTINCT FROM p_project OR NOT w.is_default OR w.owner_deleted_at IS NOT NULL
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
 IF (SELECT count(*) FROM public.cloud_workbench_connections WHERE user_id=p_user AND state<>'released')>=20 THEN
   RAISE EXCEPTION 'workspace_connection_limit' USING ERRCODE='53400', DETAIL='owner';
 END IF;
 IF w.next_task_uid>59999 THEN RAISE EXCEPTION 'workbench_task_identity_exhausted'; END IF;
 INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch)
 VALUES(p_id,w.id,w.generation,p_user,p_project,w.next_task_uid,
   w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',w.next_task_uid),w.binding,w.compute_epoch) RETURNING * INTO c;
 UPDATE public.cloud_workbenches SET next_task_uid=next_task_uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
 RETURN to_jsonb(c);
END $$;

COMMIT;
