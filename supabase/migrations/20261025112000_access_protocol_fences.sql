BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

-- Keep the existing repository SQL authority interface, including checked final
-- publication. Managed credentials add a parent fence; unlinked credentials do
-- not gain a registration, resource or permission through this migration.
CREATE OR REPLACE FUNCTION public.resolve_git_runtime_credential(p_key_hash text)
RETURNS TABLE(credential_id text,org_id text,project_id text,access_surface_id text,target_kind text,
 scope_id text,path_prefix text,excludes jsonb,target_max_mode text,user_id uuid,effective_mode text)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT c.id,c.org_id,c.project_id,c.access_surface_id,
   CASE WHEN s.scope_id IS NULL THEN 'project_root' ELSE 'scope' END,s.scope_id,
   coalesce(rs.path,''),coalesce(rs.exclude,'[]'::jsonb)||coalesce((SELECT jsonb_agg(d.path ORDER BY d.path)
     FROM public.repository_scopes d WHERE s.scope_id IS NOT NULL AND d.project_id=s.project_id
       AND d.id<>s.scope_id AND left(d.path,length(rs.path)+1)=rs.path||'/'),'[]'::jsonb),
   coalesce(rs.max_mode,'rw'),c.user_id,
   CASE WHEN c.grant_mode<>'rw' OR coalesce(rs.max_mode,'rw')<>'rw'
     OR coalesce(s.config->>'mode','rw')<>'rw' OR (c.credential_lifecycle='user' AND role.effective_role='viewer')
     OR NOT public.access_credential_grant_valid(c.id,coalesce(c.access_channel,'git'),true)
   THEN 'r' ELSE 'rw' END
 FROM public.access_surface_credentials c
 JOIN public.projects p ON p.id=c.project_id AND p.org_id=c.org_id AND p.lifecycle_status='ready'
 JOIN public.access_surfaces s ON s.id=c.access_surface_id AND s.project_id=c.project_id AND s.org_id=c.org_id
   AND s.kind='git_remote' AND s.status='active'
 LEFT JOIN public.repository_scopes rs ON rs.id=s.scope_id AND rs.project_id=s.project_id
 LEFT JOIN LATERAL public.resolve_project_role(c.project_id,c.user_id) role ON c.credential_lifecycle='user'
 WHERE c.key_hash=p_key_hash AND c.credential_type='git_http_token' AND c.status='active'
   AND (c.expires_at IS NULL OR c.expires_at>clock_timestamp()) AND (s.scope_id IS NULL OR rs.id IS NOT NULL)
   AND (c.credential_lifecycle<>'user' OR (c.user_id IS NOT NULL AND role.org_id=c.org_id AND role.effective_role IS NOT NULL))
   AND public.access_credential_grant_valid(c.id,coalesce(c.access_channel,'git'))
 LIMIT 1
$$;

CREATE FUNCTION public.resolve_access_git_credential(p_key_hash text,p_channel text DEFAULT 'git')
RETURNS jsonb LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT to_jsonb(f)||jsonb_build_object('registration_id',g.registration_id,'access_grant_id',g.id)
 FROM public.resolve_git_runtime_credential(p_key_hash) f
 JOIN public.access_surface_credentials c ON c.id=f.credential_id
 LEFT JOIN public.access_grants g ON g.id=c.access_grant_id
 WHERE p_channel IN ('git','cli') AND (c.access_grant_id IS NULL OR c.access_channel=p_channel)
$$;

CREATE FUNCTION public.access_managed_credential(p_key_hash text,p_channel text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_surface_credentials;
BEGIN
 SELECT * INTO c FROM public.access_surface_credentials WHERE key_hash=p_key_hash
   AND credential_type='bearer_token' AND status='active' AND expires_at>clock_timestamp()
   AND access_grant_id IS NOT NULL AND access_channel=p_channel;
 IF NOT FOUND OR p_channel<>'mcp' OR NOT public.access_credential_grant_valid(c.id,p_channel) THEN RETURN NULL; END IF;
 RETURN to_jsonb(c)||jsonb_build_object('authority',public.access_grant_context(c.access_grant_id,p_channel));
END $$;

CREATE OR REPLACE FUNCTION public.workspace_ssh_grant_consume(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE ticket public.workspace_ssh_grants; parent public.access_grants; context jsonb;
BEGIN
 SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'ssh_grant_unavailable' USING ERRCODE='42501'; END IF;
 IF ticket.access_grant_id IS NOT NULL THEN
   SELECT * INTO parent FROM public.access_grants WHERE id=ticket.access_grant_id;
   PERFORM 1 FROM public.access_registrations WHERE id=parent.registration_id FOR SHARE;
   PERFORM 1 FROM public.access_grants WHERE id=parent.id FOR UPDATE;
   context:=public.access_grant_context(parent.id,'ssh');
   IF parent.target_kind<>'workspace' OR parent.workbench_id<>ticket.workbench_id
     OR parent.project_id<>ticket.project_id OR parent.approved_by<>ticket.user_id THEN
     RAISE EXCEPTION 'ssh_grant_unavailable' USING ERRCODE='42501'; END IF;
 END IF;
 UPDATE public.workspace_ssh_grants SET used_at=clock_timestamp()
 WHERE id=p_id AND used_at IS NULL AND revoked_at IS NULL AND expires_at>clock_timestamp() RETURNING * INTO ticket;
 IF NOT FOUND THEN RAISE EXCEPTION 'ssh_grant_unavailable' USING ERRCODE='42501'; END IF;
 IF ticket.access_grant_id IS NOT NULL THEN
   INSERT INTO public.access_sessions(id,grant_id,registration_id,channel,credential_id,workbench_id,generation,lease_until)
   VALUES(ticket.id,parent.id,parent.registration_id,'ssh',ticket.id::text,ticket.workbench_id,ticket.generation,
     least(clock_timestamp()+interval '45 seconds',parent.expires_at));
   IF parent.single_session THEN UPDATE public.access_grants SET consumed_by=ticket.id WHERE id=parent.id; END IF;
   INSERT INTO public.access_lifecycle_events(registration_id,grant_id,session_id,event)
   VALUES(parent.registration_id,parent.id,ticket.id,'session.admitted');
 END IF;
 RETURN to_jsonb(ticket);
END $$;

-- Admission/renewal race fences run in Workspace's transaction, independent of
-- the gateway heartbeat. Workspace remains the only writer of execution state.
CREATE FUNCTION public.access_workspace_connection_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE ticket public.workspace_ssh_grants; s public.access_sessions; g public.access_grants; context jsonb;
BEGIN
 SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=NEW.id;
 IF TG_OP='UPDATE' THEN NEW.access_session_id:=OLD.access_session_id; END IF;
 IF ticket.access_grant_id IS NULL AND NEW.access_session_id IS NULL
   AND NOT EXISTS(SELECT 1 FROM public.access_sessions WHERE id=NEW.id) THEN RETURN NEW; END IF;
 NEW.access_session_id:=NEW.id;
 IF TG_OP='UPDATE' AND NEW.state IN ('stopping','released') THEN
   UPDATE public.access_sessions SET state=CASE NEW.state WHEN 'released' THEN 'closed' ELSE 'stopping' END,
     closed_at=CASE WHEN NEW.state='released' THEN coalesce(closed_at,clock_timestamp()) END
   WHERE id=NEW.id;
   RETURN NEW;
 END IF;
 IF ticket.access_grant_id IS NULL THEN RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
 SELECT * INTO g FROM public.access_grants WHERE id=ticket.access_grant_id;
 PERFORM 1 FROM public.access_registrations WHERE id=g.registration_id FOR SHARE;
 PERFORM 1 FROM public.access_grants WHERE id=g.id FOR SHARE;
 context:=public.access_grant_context(g.id,'ssh',NEW.id);
 SELECT * INTO s FROM public.access_sessions WHERE id=NEW.id FOR UPDATE;
 IF NOT FOUND OR s.state<>'active' OR s.lease_until<=clock_timestamp() OR ticket.revoked_at IS NOT NULL
   OR s.workbench_id<>NEW.workbench_id OR s.generation<>NEW.generation
   OR g.workbench_id<>NEW.workbench_id OR g.project_id<>NEW.project_id OR g.approved_by<>NEW.user_id THEN
   RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
 NEW.lease_until:=least(NEW.lease_until,(context->'grant'->>'expires_at')::timestamptz);
 UPDATE public.access_sessions SET lease_until=NEW.lease_until WHERE id=NEW.id;
 RETURN NEW;
END $$;
CREATE TRIGGER access_workspace_connection_fence BEFORE INSERT OR UPDATE ON public.cloud_workbench_connections
 FOR EACH ROW EXECUTE FUNCTION public.access_workspace_connection_fence();

CREATE FUNCTION public.access_session_command(p_action text,p_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE s public.access_sessions; ticket public.workspace_ssh_grants; result jsonb:='[]';
 c public.cloud_workbench_connections;
BEGIN
 IF p_action='ready' THEN
   PERFORM 1 FROM public.access_sessions LIMIT 1;
   RETURN '{}'::jsonb;
 ELSIF p_action='finish_unallocated' THEN
   UPDATE public.access_sessions SET state='closed',closed_at=clock_timestamp()
   WHERE id=p_id AND state<>'closed' AND NOT EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE id=p_id);
   RETURN '{}'::jsonb;
 ELSIF p_action='pending' THEN
   FOR s IN SELECT * FROM public.access_sessions WHERE state<>'closed' ORDER BY lease_until,id LIMIT 100 FOR UPDATE SKIP LOCKED LOOP
     SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=s.id;
     BEGIN
       PERFORM public.access_grant_context(s.grant_id,s.channel,s.id);
       IF s.lease_until<=clock_timestamp() OR ticket.id IS NULL OR ticket.revoked_at IS NOT NULL THEN s.state:='stopping'; END IF;
     EXCEPTION WHEN insufficient_privilege THEN s.state:='stopping';
     END;
     IF s.state='stopping' THEN
       UPDATE public.access_sessions SET state='stopping' WHERE id=s.id AND state='active';
       SELECT * INTO c FROM public.cloud_workbench_connections WHERE id=s.id;
       IF NOT FOUND OR c.state='released' THEN
         UPDATE public.access_sessions SET state='closed',closed_at=clock_timestamp() WHERE id=s.id;
       ELSE result:=result||jsonb_build_array(to_jsonb(c)); END IF;
     END IF;
   END LOOP;
   UPDATE public.access_lifecycle_events e SET delivered_at=clock_timestamp()
   WHERE delivered_at IS NULL AND NOT EXISTS(SELECT 1 FROM public.access_sessions live WHERE live.state<>'closed'
     AND ((e.session_id IS NOT NULL AND live.id=e.session_id) OR (e.session_id IS NULL AND e.grant_id IS NOT NULL AND live.grant_id=e.grant_id)
       OR (e.session_id IS NULL AND e.grant_id IS NULL AND live.registration_id=e.registration_id)));
   RETURN result;
 END IF;
 RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
END $$;

REVOKE ALL ON FUNCTION public.resolve_access_git_credential(text,text),public.access_managed_credential(text,text),
 public.access_workspace_connection_fence(),public.access_session_command(text,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_access_git_credential(text,text),public.access_managed_credential(text,text),
 public.access_session_command(text,uuid) TO service_role;
COMMIT;
