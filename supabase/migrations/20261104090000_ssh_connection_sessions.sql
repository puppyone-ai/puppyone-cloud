-- Additive: one SSH connection is one Access session, and every command run over
-- it is its own Workspace connection under that session, so a client keeps one
-- authenticated connection instead of signing in again for each command.
-- 1. cloud_workbench_connection_acquire_in_session records which session a command
--    belongs to. The existing acquire is that call with no session, so a previous
--    gateway keeps its one-command sessions unchanged.
-- 2. The connection fence finds the session through access_session_id, falling
--    back to the row's own id for one-command sessions. A command ending no longer
--    ends a session that outlives it.
-- 3. access_session_command adds 'renew', the connection's heartbeat, which holds
--    the session with no command running, and 'finish', which closes it once its
--    commands are released. Expiry and revocation reclaim every command of a session.
-- Function signatures are unchanged, so callers from the previous release keep working.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

CREATE INDEX cloud_workbench_connections_session ON public.cloud_workbench_connections(access_session_id)
 WHERE state<>'released';

CREATE FUNCTION public.cloud_workbench_connection_acquire_in_session(p_id uuid,p_session uuid,p_workbench uuid,p_generation uuid,
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
 INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch,
   access_session_id)
 VALUES(p_id,w.id,w.generation,p_user,p_project,w.next_task_uid,
   w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',w.next_task_uid),w.binding,w.compute_epoch,
   p_session) RETURNING * INTO c;
 UPDATE public.cloud_workbenches SET next_task_uid=next_task_uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
 RETURN to_jsonb(c);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_acquire(p_id uuid,p_workbench uuid,p_generation uuid,
 p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT public.cloud_workbench_connection_acquire_in_session(p_id,NULL,p_workbench,p_generation,p_user,p_project,p_revision)
$$;

-- Admission/renewal race fences run in Workspace's transaction, independent of
-- the gateway heartbeat. Workspace remains the only writer of execution state.
CREATE OR REPLACE FUNCTION public.access_workspace_connection_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE ticket public.workspace_ssh_grants; s public.access_sessions; g public.access_grants; context jsonb;
 owning_session uuid;
BEGIN
 IF TG_OP='UPDATE' THEN NEW.access_session_id:=OLD.access_session_id; END IF;
 -- The session that owns this row: its connection's, or the row itself for a one-command session.
 owning_session:=coalesce(NEW.access_session_id,NEW.id);
 SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=owning_session;
 IF ticket.access_grant_id IS NULL AND NEW.access_session_id IS NULL
   AND NOT EXISTS(SELECT 1 FROM public.access_sessions WHERE id=NEW.id) THEN RETURN NEW; END IF;
 NEW.access_session_id:=owning_session;
 IF TG_OP='UPDATE' AND NEW.state IN ('stopping','released') THEN
   -- Only a one-command session ends with its command; a connection's session
   -- is finished by the connection, or by cleanup once its last command is released.
   IF owning_session=NEW.id THEN
     UPDATE public.access_sessions SET state=CASE NEW.state WHEN 'released' THEN 'closed' ELSE 'stopping' END,
       closed_at=CASE WHEN NEW.state='released' THEN coalesce(closed_at,clock_timestamp()) END
     WHERE id=NEW.id;
   END IF;
   RETURN NEW;
 END IF;
 IF ticket.access_grant_id IS NULL THEN RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
 SELECT * INTO g FROM public.access_grants WHERE id=ticket.access_grant_id;
 PERFORM 1 FROM public.access_registrations WHERE id=g.registration_id FOR SHARE;
 PERFORM 1 FROM public.access_grants WHERE id=g.id FOR SHARE;
 context:=public.access_grant_context(g.id,'ssh',owning_session);
 SELECT * INTO s FROM public.access_sessions WHERE id=owning_session FOR UPDATE;
 IF NOT FOUND OR s.state<>'active' OR s.lease_until<=clock_timestamp() OR ticket.revoked_at IS NOT NULL
   OR s.workbench_id<>NEW.workbench_id OR s.generation<>NEW.generation
   OR g.workbench_id<>NEW.workbench_id OR g.project_id<>NEW.project_id OR g.approved_by<>NEW.user_id THEN
   RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
 NEW.lease_until:=least(NEW.lease_until,(context->'grant'->>'expires_at')::timestamptz);
 -- A command's renewal also holds its connection's session, and never shortens it.
 UPDATE public.access_sessions
 SET lease_until=CASE WHEN owning_session=NEW.id THEN NEW.lease_until ELSE greatest(lease_until,NEW.lease_until) END
 WHERE id=owning_session;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.access_session_command(p_action text,p_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE s public.access_sessions; ticket public.workspace_ssh_grants; result jsonb:='[]';
 context jsonb; held boolean;
BEGIN
 IF p_action='ready' THEN
   PERFORM 1 FROM public.access_sessions LIMIT 1;
   RETURN '{}'::jsonb;
 ELSIF p_action='finish_unallocated' THEN
   UPDATE public.access_sessions SET state='closed',closed_at=clock_timestamp()
   WHERE id=p_id AND state<>'closed' AND NOT EXISTS(SELECT 1 FROM public.cloud_workbench_connections
     WHERE id=p_id OR access_session_id=p_id);
   RETURN '{}'::jsonb;
 ELSIF p_action='renew' THEN
   -- The connection's heartbeat: the same authority a command is fenced by, with
   -- no command required, so an idle connection keeps its session.
   SELECT * INTO s FROM public.access_sessions WHERE id=p_id;
   IF NOT FOUND THEN RAISE EXCEPTION 'access_session_ended' USING ERRCODE='42501'; END IF;
   PERFORM 1 FROM public.access_registrations WHERE id=s.registration_id FOR SHARE;
   PERFORM 1 FROM public.access_grants WHERE id=s.grant_id FOR SHARE;
   SELECT * INTO s FROM public.access_sessions WHERE id=p_id FOR UPDATE;
   SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=p_id;
   IF s.state<>'active' OR s.lease_until<=clock_timestamp() OR ticket.id IS NULL OR ticket.revoked_at IS NOT NULL THEN
     RAISE EXCEPTION 'access_session_ended' USING ERRCODE='42501'; END IF;
   context:=public.access_grant_context(s.grant_id,s.channel,s.id);
   UPDATE public.access_sessions
   SET lease_until=least(clock_timestamp()+interval '45 seconds',(context->'grant'->>'expires_at')::timestamptz)
   WHERE id=s.id RETURNING * INTO s;
   RETURN jsonb_build_object('id',s.id,'lease_until',s.lease_until);
 ELSIF p_action='finish' THEN
   -- The connection ended. Its session closes now unless a command still holds the
   -- Workspace; cleanup closes it once that command is released.
   held:=EXISTS(SELECT 1 FROM public.cloud_workbench_connections
     WHERE (id=p_id OR access_session_id=p_id) AND state<>'released');
   UPDATE public.access_sessions SET state=CASE WHEN held THEN 'stopping' ELSE 'closed' END,
     closed_at=CASE WHEN held THEN NULL ELSE clock_timestamp() END
   WHERE id=p_id AND state<>'closed';
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
       -- Every command of the session is reclaimed, not only a one-command session's own row.
       IF NOT EXISTS(SELECT 1 FROM public.cloud_workbench_connections
         WHERE (id=s.id OR access_session_id=s.id) AND state<>'released') THEN
         UPDATE public.access_sessions SET state='closed',closed_at=clock_timestamp() WHERE id=s.id;
       ELSE
         result:=result||(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) FROM public.cloud_workbench_connections c
           WHERE (c.id=s.id OR c.access_session_id=s.id) AND c.state<>'released');
       END IF;
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

REVOKE ALL ON FUNCTION public.cloud_workbench_connection_acquire_in_session(uuid,uuid,uuid,uuid,uuid,text,jsonb)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_connection_acquire_in_session(uuid,uuid,uuid,uuid,uuid,text,jsonb)
 TO service_role;
COMMIT;
