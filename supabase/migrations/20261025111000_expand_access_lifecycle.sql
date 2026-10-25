BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

-- Reset/reuse must retire issued proofs as well as the renewal key: resuming
-- the identity must not make an attacker's already-issued tokens valid again.
CREATE FUNCTION public.access_revoke_registration_credentials(p_registration uuid)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 UPDATE public.access_registration_credentials SET state='revoked'
 WHERE registration_id=p_registration AND state<>'revoked';
 UPDATE public.access_surface_credentials SET status='revoked',revoked_at=clock_timestamp()
 WHERE status='active' AND access_grant_id IN
   (SELECT id FROM public.access_grants WHERE registration_id=p_registration);
 UPDATE public.workspace_ssh_grants SET revoked_at=clock_timestamp()
 WHERE revoked_at IS NULL AND access_grant_id IN
   (SELECT id FROM public.access_grants WHERE registration_id=p_registration);
$$;
REVOKE ALL ON FUNCTION public.access_revoke_registration_credentials(uuid) FROM PUBLIC,anon,authenticated,service_role;

CREATE FUNCTION public.access_registration_command(p_action text,p_user uuid,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.access_registrations; identity uuid := (p_payload->>'id')::uuid;
 organization text := p_payload->>'owner_org_id'; next_state text := p_payload->>'state';
BEGIN
 IF p_user IS NULL OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_action='list' THEN
   RETURN coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.id) FROM
     (SELECT * FROM public.access_registrations WHERE owner_user_id=p_user ORDER BY created_at,id LIMIT 200) x),'[]');
 END IF;
 IF p_action='create' THEN
   IF organization IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members
     WHERE org_id=organization AND user_id=p_user AND role='owner') THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   IF p_payload->>'key_hash' IS NULL OR length(p_payload->>'key_hash')<>64 THEN
     RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
   INSERT INTO public.access_registrations(id,owner_user_id,owner_org_id,name)
   VALUES(identity,p_user,organization,p_payload->>'name') ON CONFLICT(id) DO NOTHING;
   SELECT * INTO r FROM public.access_registrations WHERE id=identity FOR UPDATE;
   IF r.owner_user_id IS DISTINCT FROM p_user OR r.owner_org_id IS DISTINCT FROM organization
     OR r.name IS DISTINCT FROM p_payload->>'name' OR r.state<>'active' THEN
     RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501'; END IF;
   IF NOT EXISTS(SELECT 1 FROM public.access_registration_credentials WHERE registration_id=identity) THEN
     INSERT INTO public.access_registration_credentials(id,registration_id,key_hash,expires_at)
     VALUES(gen_random_uuid(),identity,p_payload->>'key_hash',clock_timestamp()+interval '90 days');
     INSERT INTO public.access_lifecycle_events(registration_id,actor_user_id,event) VALUES(identity,p_user,'registration.created');
   ELSIF NOT EXISTS(SELECT 1 FROM public.access_registration_credentials
     WHERE registration_id=identity AND key_hash=p_payload->>'key_hash' AND state='active') THEN
     RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501';
   END IF;
   RETURN to_jsonb(r);
 END IF;
 SELECT * INTO r FROM public.access_registrations WHERE id=identity FOR UPDATE;
 IF NOT FOUND OR r.owner_user_id IS DISTINCT FROM p_user THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_action='get' THEN RETURN to_jsonb(r); END IF;
 IF p_action='reset_key' THEN
   IF r.state='revoked' OR p_payload->>'key_hash' IS NULL OR length(p_payload->>'key_hash')<>64 THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   IF EXISTS(SELECT 1 FROM public.access_registration_credentials WHERE registration_id=identity
     AND state='active' AND key_hash=p_payload->>'key_hash') THEN RETURN to_jsonb(r); END IF;
   PERFORM public.access_revoke_registration_credentials(identity);
   INSERT INTO public.access_registration_credentials(id,registration_id,key_hash,expires_at)
   VALUES(gen_random_uuid(),identity,p_payload->>'key_hash',clock_timestamp()+interval '90 days');
   UPDATE public.access_sessions SET state='stopping' WHERE registration_id=identity AND state='active';
   INSERT INTO public.access_lifecycle_events(registration_id,actor_user_id,event)
   VALUES(identity,p_user,'registration.key_reset');
   RETURN to_jsonb(r);
 END IF;
 IF p_action='grants' THEN
   RETURN coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.created_at,g.id)
     FROM public.access_grants g WHERE registration_id=identity),'[]');
 END IF;
 IF p_action<>'state' OR next_state NOT IN ('active','suspended','revoked') OR next_state IS NULL
   OR (r.state='revoked' AND next_state<>'revoked') THEN
   RAISE EXCEPTION 'access_invalid_transition' USING ERRCODE='22023'; END IF;
 UPDATE public.access_registrations SET state=next_state,revision=revision+1,updated_at=clock_timestamp()
 WHERE id=identity RETURNING * INTO r;
 IF next_state<>'active' THEN
   UPDATE public.access_sessions SET state='stopping' WHERE registration_id=identity AND state='active';
 END IF;
 IF next_state='revoked' THEN
   PERFORM public.access_revoke_registration_credentials(identity);
 END IF;
 INSERT INTO public.access_lifecycle_events(registration_id,actor_user_id,event)
 VALUES(identity,p_user,'registration.'||next_state);
 RETURN to_jsonb(r);
END $$;

CREATE FUNCTION public.access_grant_command(p_action text,p_user uuid,p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE invitation public.access_invitations; request public.access_requests;
 registration public.access_registrations; g public.access_grants;
 project text:=p_payload->>'project_id'; organization text; workbench uuid:=(p_payload->>'workbench_id')::uuid;
 identity uuid:=(p_payload->>'id')::uuid; caps text[]; channels text[]; expiry timestamptz;
 next_state text:=p_payload->>'state'; decision text:=p_payload->>'decision';
BEGIN
 IF p_user IS NULL OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 IF p_action='invite' THEN
   organization:=public.access_assert_owner(p_user,project,workbench);
   SELECT array_agg(value) INTO caps FROM jsonb_array_elements_text(p_payload->'capabilities');
   SELECT array_agg(value) INTO channels FROM jsonb_array_elements_text(p_payload->'channels');
   INSERT INTO public.access_invitations(id,key_hash,project_id,org_id,created_by,target_kind,workbench_id,
     capabilities,channels,recipient_user_id,max_grant_seconds,expires_at,max_approvals)
   VALUES(identity,p_payload->>'key_hash',project,organization,p_user,p_payload->>'target_kind',workbench,
     caps,channels,(p_payload->>'recipient_user_id')::uuid,(p_payload->>'max_grant_seconds')::integer,
     (p_payload->>'expires_at')::timestamptz,coalesce((p_payload->>'max_approvals')::integer,1))
   ON CONFLICT(id) DO NOTHING;
   SELECT * INTO invitation FROM public.access_invitations WHERE id=identity;
   IF invitation.created_by IS DISTINCT FROM p_user OR invitation.project_id IS DISTINCT FROM project
     OR invitation.key_hash IS DISTINCT FROM p_payload->>'key_hash' OR invitation.capabilities IS DISTINCT FROM caps
     OR invitation.channels IS DISTINCT FROM channels OR invitation.workbench_id IS DISTINCT FROM workbench
     OR invitation.target_kind IS DISTINCT FROM p_payload->>'target_kind'
     OR invitation.recipient_user_id IS DISTINCT FROM (p_payload->>'recipient_user_id')::uuid
     OR invitation.max_grant_seconds IS DISTINCT FROM (p_payload->>'max_grant_seconds')::integer
     OR invitation.expires_at IS DISTINCT FROM (p_payload->>'expires_at')::timestamptz
     OR invitation.max_approvals IS DISTINCT FROM coalesce((p_payload->>'max_approvals')::integer,1) THEN
     RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501'; END IF;
   RETURN to_jsonb(invitation)-'key_hash';
 ELSIF p_action IN ('invitations','requests','grants','sessions') THEN
   PERFORM public.access_assert_owner(p_user,project);
   IF p_action='invitations' THEN RETURN coalesce((SELECT jsonb_agg(to_jsonb(x)-'key_hash') FROM
     (SELECT * FROM public.access_invitations WHERE project_id=project ORDER BY created_at DESC,id LIMIT 200) x),'[]'); END IF;
   IF p_action='requests' THEN RETURN coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM
     (SELECT r.* FROM public.access_requests r JOIN public.access_invitations i ON i.id=r.invitation_id
      WHERE i.project_id=project ORDER BY r.created_at DESC,r.id LIMIT 200) x),'[]'); END IF;
   IF p_action='grants' THEN RETURN coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM
     (SELECT * FROM public.access_grants WHERE project_id=project ORDER BY created_at DESC,id LIMIT 200) x),'[]'); END IF;
   RETURN coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM
     (SELECT s.* FROM public.access_sessions s JOIN public.access_grants parent ON parent.id=s.grant_id
      WHERE parent.project_id=project ORDER BY s.created_at DESC,s.id LIMIT 200) x),'[]');
 ELSIF p_action='request' THEN
   SELECT * INTO registration FROM public.access_registrations WHERE id=(p_payload->>'registration_id')::uuid FOR UPDATE;
   IF NOT FOUND OR registration.owner_user_id IS DISTINCT FROM p_user OR registration.state<>'active' THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   SELECT * INTO invitation FROM public.access_invitations WHERE key_hash=p_payload->>'key_hash' OR key_hash IN (SELECT jsonb_array_elements_text(p_payload->'key_hashes')) FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   SELECT * INTO request FROM public.access_requests WHERE invitation_id=invitation.id AND registration_id=registration.id;
   IF FOUND THEN RETURN to_jsonb(request); END IF;
   IF invitation.state<>'active' OR invitation.expires_at<=clock_timestamp()
     OR invitation.approvals>=invitation.max_approvals
     OR (invitation.recipient_user_id IS NOT NULL AND invitation.recipient_user_id<>p_user)
     OR (registration.owner_org_id IS NOT NULL AND registration.owner_org_id<>invitation.org_id) THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   PERFORM public.access_assert_owner(invitation.created_by,invitation.project_id,invitation.workbench_id);
   INSERT INTO public.access_requests(id,invitation_id,registration_id,applicant_user_id)
   VALUES(identity,invitation.id,registration.id,p_user) ON CONFLICT(invitation_id,registration_id) DO NOTHING;
   SELECT * INTO request FROM public.access_requests WHERE invitation_id=invitation.id AND registration_id=registration.id;
   RETURN to_jsonb(request);
 ELSIF p_action IN ('decide','revoke_invitation') THEN
   IF p_action='decide' THEN
     SELECT * INTO request FROM public.access_requests WHERE id=identity;
     IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
     SELECT * INTO invitation FROM public.access_invitations WHERE id=request.invitation_id FOR UPDATE;
     SELECT * INTO request FROM public.access_requests WHERE id=identity FOR UPDATE;
   ELSE SELECT * INTO invitation FROM public.access_invitations WHERE id=identity FOR UPDATE;
   END IF;
   IF invitation.project_id IS DISTINCT FROM project THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   PERFORM public.access_assert_owner(p_user,project,invitation.workbench_id);
   IF p_action='revoke_invitation' THEN
     UPDATE public.access_invitations SET state='revoked' WHERE id=identity RETURNING * INTO invitation;
     RETURN to_jsonb(invitation)-'key_hash';
   END IF;
   IF decision NOT IN ('approve','reject') OR decision IS NULL THEN
     RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023'; END IF;
   IF request.state='approved' AND decision='approve' THEN
     SELECT * INTO g FROM public.access_grants WHERE request_id=request.id;
     RETURN to_jsonb(g);
   ELSIF request.state='rejected' AND decision='reject' THEN RETURN to_jsonb(request);
   ELSIF request.state<>'pending' THEN RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501'; END IF;
   IF invitation.state<>'active' OR invitation.expires_at<=clock_timestamp()
     OR invitation.approvals>=invitation.max_approvals THEN
     RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   IF decision='approve' THEN
     SELECT array_agg(value) INTO caps FROM jsonb_array_elements_text(p_payload->'capabilities');
     SELECT array_agg(value) INTO channels FROM jsonb_array_elements_text(p_payload->'channels');
     caps:=coalesce(caps,invitation.capabilities); channels:=coalesce(channels,invitation.channels);
     expiry:=(p_payload->>'expires_at')::timestamptz;
     IF NOT(caps<@invitation.capabilities) OR NOT(channels<@invitation.channels)
       OR (invitation.max_grant_seconds IS NOT NULL AND (expiry IS NULL
         OR expiry>clock_timestamp()+make_interval(secs=>invitation.max_grant_seconds))) THEN
       RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
     IF NOT EXISTS(SELECT 1 FROM public.access_registrations WHERE id=request.registration_id AND state='active') THEN
       RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
     INSERT INTO public.access_grants(id,request_id,registration_id,project_id,org_id,approved_by,
       target_kind,workbench_id,capabilities,channels,expires_at,single_session)
     VALUES(gen_random_uuid(),request.id,request.registration_id,project,invitation.org_id,p_user,
       invitation.target_kind,invitation.workbench_id,caps,channels,expiry,coalesce((p_payload->>'single_session')::boolean,false))
     RETURNING * INTO g;
     UPDATE public.access_invitations SET approvals=approvals+1 WHERE id=invitation.id;
   END IF;
   UPDATE public.access_requests SET state=CASE decision WHEN 'approve' THEN 'approved' ELSE 'rejected' END,
     decided_by=p_user,decided_at=clock_timestamp() WHERE id=request.id RETURNING * INTO request;
   INSERT INTO public.access_lifecycle_events(registration_id,grant_id,actor_user_id,event)
   VALUES(request.registration_id,g.id,p_user,'request.'||request.state);
   RETURN CASE decision WHEN 'approve' THEN to_jsonb(g) ELSE to_jsonb(request) END;
 ELSIF p_action='close_session' THEN
   SELECT parent.* INTO g FROM public.access_grants parent JOIN public.access_sessions s ON s.grant_id=parent.id
   WHERE s.id=identity;
   IF NOT FOUND OR g.project_id IS DISTINCT FROM project THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   PERFORM public.access_assert_owner(p_user,project,g.workbench_id);
   UPDATE public.access_sessions SET state='stopping' WHERE id=identity AND state='active';
   INSERT INTO public.access_lifecycle_events(registration_id,grant_id,session_id,actor_user_id,event)
   VALUES(g.registration_id,g.id,identity,p_user,'session.stop_requested');
   RETURN (SELECT to_jsonb(s) FROM public.access_sessions s WHERE id=identity);
 ELSIF p_action='grant_state' THEN
   SELECT * INTO g FROM public.access_grants WHERE id=identity FOR UPDATE;
   IF NOT FOUND OR g.project_id IS DISTINCT FROM project THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
   PERFORM public.access_assert_owner(p_user,project,g.workbench_id);
   IF next_state NOT IN ('active','suspended','revoked') OR next_state IS NULL
     OR (g.state='revoked' AND next_state<>'revoked') THEN
     RAISE EXCEPTION 'access_invalid_transition' USING ERRCODE='22023'; END IF;
   UPDATE public.access_grants SET state=next_state,revision=revision+1 WHERE id=identity RETURNING * INTO g;
   IF next_state<>'active' THEN UPDATE public.access_sessions SET state='stopping' WHERE grant_id=identity AND state='active'; END IF;
   INSERT INTO public.access_lifecycle_events(registration_id,grant_id,actor_user_id,event)
   VALUES(g.registration_id,g.id,p_user,'grant.'||next_state);
   RETURN to_jsonb(g);
 END IF;
 RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
END $$;

CREATE FUNCTION public.access_issue_credential(p_key_hashes text[],p_next_key_hash text,p_request uuid,
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
   IF w.state<>'running' OR w.owner_deleted_at IS NOT NULL THEN RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000'; END IF;
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
