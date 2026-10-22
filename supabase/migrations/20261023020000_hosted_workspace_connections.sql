-- Additive: external connection occupancy is owned by Workspace, not a Provider.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';
CREATE TABLE public.cloud_workbench_connections (
 id uuid PRIMARY KEY,
 workbench_id uuid NOT NULL REFERENCES public.cloud_workbenches(id) ON DELETE CASCADE,
 generation uuid NOT NULL,
 user_id uuid NOT NULL,
 project_id text NOT NULL,
 task_uid integer NOT NULL CHECK(task_uid BETWEEN 20000 AND 59999),
 resource jsonb NOT NULL CHECK(jsonb_typeof(resource)='object'),
 binding jsonb NOT NULL CHECK(jsonb_typeof(binding)='object'),
 compute_epoch bigint NOT NULL,
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','stopping','released')),
 lease_until timestamptz NOT NULL DEFAULT clock_timestamp()+interval '45 seconds',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 released_at timestamptz,
 UNIQUE(workbench_id,generation,task_uid)
);
CREATE INDEX cloud_workbench_connections_cleanup ON public.cloud_workbench_connections(lease_until) WHERE state<>'released';
ALTER TABLE public.cloud_workbench_connections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cloud_workbench_connections FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.cloud_workbench_connections TO service_role;

CREATE TABLE public.workspace_ssh_grants (
 id uuid PRIMARY KEY,
 workbench_id uuid NOT NULL REFERENCES public.cloud_workbenches(id) ON DELETE CASCADE,
 generation uuid NOT NULL,
 user_id uuid NOT NULL,
 project_id text NOT NULL,
 public_key text NOT NULL CHECK(length(public_key) BETWEEN 40 AND 8192),
 expires_at timestamptz NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+interval '1 hour'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 used_at timestamptz,
 revoked_at timestamptz
);
CREATE INDEX workspace_ssh_grants_owner ON public.workspace_ssh_grants(user_id,project_id);
ALTER TABLE public.workspace_ssh_grants ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.workspace_ssh_grants FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.workspace_ssh_grants TO service_role;

CREATE FUNCTION public.cloud_workbench_connection_acquire(p_id uuid,p_workbench uuid,p_generation uuid,
 p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; c public.cloud_workbench_connections;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.user_id IS DISTINCT FROM p_user
   OR w.project_id IS DISTINCT FROM p_project OR NOT w.is_default OR w.owner_deleted_at IS NOT NULL
   OR w.state<>'running' OR w.resource IS NULL OR coalesce(w.resource->>'allocation_pending','true')<>'false' THEN
   RAISE EXCEPTION 'workspace_connection_denied' USING ERRCODE='42501';
 END IF;
 IF p_id IS NULL OR EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE id=p_id) THEN
   RAISE EXCEPTION 'workspace_connection_already_used' USING ERRCODE='42501';
 END IF;
 IF w.next_task_uid>59999 THEN RAISE EXCEPTION 'workbench_task_identity_exhausted'; END IF;
 INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch)
 VALUES(p_id,w.id,w.generation,p_user,p_project,w.next_task_uid,
   w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',w.next_task_uid),w.binding,w.compute_epoch) RETURNING * INTO c;
 UPDATE public.cloud_workbenches SET next_task_uid=next_task_uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
 RETURN to_jsonb(c);
END $$;

CREATE FUNCTION public.cloud_workbench_connection_update(p_id uuid,p_generation uuid,p_action text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.cloud_workbench_connections; w public.cloud_workbenches;
BEGIN
 -- Lock order matches acquisition and lifecycle (workbench before connection).
 SELECT * INTO c FROM public.cloud_workbench_connections WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'workspace_connection_missing'; END IF;
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=c.workbench_id FOR UPDATE;
 SELECT * INTO c FROM public.cloud_workbench_connections WHERE id=p_id FOR UPDATE;
 IF c.generation IS DISTINCT FROM p_generation OR w.generation IS DISTINCT FROM p_generation THEN
   RAISE EXCEPTION 'workspace_connection_fenced';
 END IF;
 IF p_action='renew' THEN
   IF c.state<>'active' OR c.lease_until<=clock_timestamp() OR w.state<>'running'
     OR w.owner_deleted_at IS NOT NULL OR w.binding IS DISTINCT FROM c.binding OR w.compute_epoch<>c.compute_epoch
     OR (c.resource-'task_execution_id'-'task_uid') IS DISTINCT FROM w.resource THEN
     RAISE EXCEPTION 'workspace_connection_expired';
   END IF;
   UPDATE public.cloud_workbench_connections SET lease_until=clock_timestamp()+interval '45 seconds' WHERE id=p_id RETURNING * INTO c;
 ELSIF p_action='expire' THEN
   IF c.state='active' AND c.lease_until<=clock_timestamp() THEN
     UPDATE public.cloud_workbench_connections SET state='stopping' WHERE id=p_id RETURNING * INTO c;
   END IF;
 ELSIF p_action='stop' THEN
   UPDATE public.cloud_workbench_connections SET state=CASE WHEN state='released' THEN state ELSE 'stopping' END WHERE id=p_id RETURNING * INTO c;
 ELSIF p_action='release' THEN
   IF c.state NOT IN ('stopping','released') THEN RAISE EXCEPTION 'workspace_connection_cleanup_required'; END IF;
   UPDATE public.cloud_workbench_connections SET state='released',released_at=coalesce(released_at,clock_timestamp()) WHERE id=p_id RETURNING * INTO c;
   UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() WHERE id=w.id;
 ELSE RAISE EXCEPTION 'workspace_connection_invalid_action'; END IF;
 RETURN to_jsonb(c);
END $$;

CREATE FUNCTION public.cloud_workbench_connection_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='cloud_workbench_connections' THEN
   IF OLD.state<>'released' THEN RAISE EXCEPTION 'workspace_connection_cleanup_required'; END IF;
   RETURN OLD;
 END IF;
 IF (TG_OP='DELETE' OR NEW.generation IS DISTINCT FROM OLD.generation OR NEW.state IS DISTINCT FROM OLD.state)
   AND EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE workbench_id=OLD.id AND state<>'released') THEN
   RAISE EXCEPTION 'workspace_connection_cleanup_required';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cloud_workbench_connection_delete_guard BEFORE DELETE ON public.cloud_workbench_connections
 FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_connection_guard();
CREATE TRIGGER cloud_workbench_connection_resource_guard BEFORE UPDATE OF state,generation OR DELETE ON public.cloud_workbenches
 FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_connection_guard();

CREATE OR REPLACE FUNCTION public.cloud_workbench_has_occupants(p_workbench uuid,p_generation uuid)
RETURNS boolean LANGUAGE sql SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.cloud_workbench_tasks WHERE workbench_id=p_workbench AND generation=p_generation AND state='active')
 OR EXISTS(SELECT 1 FROM public.agent_runs WHERE workbench_id=p_workbench AND state IN ('queued','running','waiting_approval','publishing'))
 OR EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE workbench_id=p_workbench AND generation=p_generation AND state<>'released')
$$;
REVOKE ALL ON FUNCTION public.cloud_workbench_connection_acquire(uuid,uuid,uuid,uuid,text,jsonb),
 public.cloud_workbench_connection_update(uuid,uuid,text),public.cloud_workbench_connection_guard() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_connection_acquire(uuid,uuid,uuid,uuid,text,jsonb),
 public.cloud_workbench_connection_update(uuid,uuid,text) TO service_role;
CREATE FUNCTION public.workspace_ssh_grant_consume(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE g public.workspace_ssh_grants;
BEGIN
 UPDATE public.workspace_ssh_grants SET used_at=clock_timestamp()
 WHERE id=p_id AND used_at IS NULL AND revoked_at IS NULL AND expires_at>clock_timestamp() RETURNING * INTO g;
 IF NOT FOUND THEN RAISE EXCEPTION 'ssh_grant_unavailable' USING ERRCODE='42501'; END IF;
 RETURN to_jsonb(g);
END $$;
REVOKE ALL ON FUNCTION public.workspace_ssh_grant_consume(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.workspace_ssh_grant_consume(uuid) TO service_role;
COMMIT;
