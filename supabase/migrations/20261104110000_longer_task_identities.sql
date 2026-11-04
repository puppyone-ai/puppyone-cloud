-- Expand: a Workspace has about two billion process identities instead of 40,000.
-- Each command and Agent run gets one, the GID its processes run under and are
-- stopped by, and a machine never hands one out twice. Now that every command
-- takes one, 40,000 could run out within days of continuous work.
-- 1. Identities run 20000-59999 as before, then 100000-2000000000. The gap keeps
--    commands out of every group the machine image has (nogroup is 65534).
-- 2. cloud_workbench_task_uid gives the next identity, still in increasing order
--    and never reused. Task and connection acquisition take theirs from it.
-- A machine whose worker predates the wider range refuses an identity past 59999,
-- which is where the old range ran out anyway; the new worker accepts it.
-- Function signatures are unchanged, so callers from the previous release keep working.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

DO $$
DECLARE narrow record;
BEGIN
 FOR narrow IN SELECT conrelid::regclass AS relation,conname FROM pg_constraint
   WHERE contype='c' AND conrelid IN ('public.cloud_workbench_connections'::regclass,
     'public.cloud_workbench_tasks'::regclass,'public.cloud_workbenches'::regclass)
     AND pg_get_constraintdef(oid) ~ 'task_uid' LOOP
   EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',narrow.relation,narrow.conname);
 END LOOP;
END $$;
ALTER TABLE public.cloud_workbench_connections ADD CONSTRAINT cloud_workbench_connections_task_uid_range
 CHECK (task_uid BETWEEN 20000 AND 59999 OR task_uid BETWEEN 100000 AND 2000000000);
ALTER TABLE public.cloud_workbench_tasks ADD CONSTRAINT cloud_workbench_tasks_task_uid_range
 CHECK (task_uid BETWEEN 20000 AND 59999 OR task_uid BETWEEN 100000 AND 2000000000);
ALTER TABLE public.cloud_workbenches ADD CONSTRAINT cloud_workbenches_next_task_uid_range
 CHECK (next_task_uid BETWEEN 20000 AND 60000 OR next_task_uid BETWEEN 100000 AND 2000000001);

CREATE FUNCTION public.cloud_workbench_task_uid(w public.cloud_workbenches)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE uid integer:=CASE WHEN w.next_task_uid BETWEEN 60000 AND 99999 THEN 100000 ELSE w.next_task_uid END;
BEGIN
 IF uid>2000000000 THEN RAISE EXCEPTION 'workbench_task_identity_exhausted'; END IF;
 RETURN uid;
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_task_acquire(p_run uuid,p_execution uuid,p_fence bigint,p_generation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs; w public.cloud_workbenches; t public.cloud_workbench_tasks;
    status jsonb; resource_value jsonb; uid integer;
BEGIN
    IF p_run IS NULL OR p_execution IS NULL OR p_fence IS NULL OR p_generation IS NULL THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    r:=public.agent_run_assert(p_run,p_execution,p_fence);
    status:=public.agent_run_guard(p_run,p_execution,p_fence);
    IF status->>'code' IS NOT NULL THEN RAISE EXCEPTION 'workbench_task_admission_denied'; END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=r.workbench_id FOR UPDATE;
    IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.user_id IS DISTINCT FROM r.user_id
        OR w.project_id IS DISTINCT FROM r.project_id OR w.owner_deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    IF w.state<>'running' OR w.resource IS NULL OR coalesce(w.resource->>'allocation_pending','true')<>'false' THEN
        RAISE EXCEPTION 'workbench_not_running';
    END IF;
    SELECT * INTO t FROM public.cloud_workbench_tasks WHERE execution_id=p_execution;
    IF FOUND THEN
        IF t.run_id<>p_run OR t.fence<>p_fence OR t.generation<>p_generation OR t.workbench_id<>w.id THEN
            RAISE EXCEPTION 'workbench_task_binding_mismatch';
        END IF;
        IF t.state<>'active' THEN RAISE EXCEPTION 'workbench_task_already_released'; END IF;
        RETURN to_jsonb(t);
    END IF;
    -- A new execution cannot race its own unconfirmed predecessor. It does not
    -- wait for unrelated Sessions sharing the computer.
    IF EXISTS(SELECT 1 FROM public.cloud_workbench_tasks WHERE run_id=p_run AND state='active') THEN
        RAISE EXCEPTION 'workbench_task_cleanup_required';
    END IF;
    uid:=public.cloud_workbench_task_uid(w);
    resource_value:=w.resource || jsonb_build_object('task_execution_id',p_execution,
        'task_uid',uid,'task_run_id',p_run,'task_fence',p_fence);
    INSERT INTO public.cloud_workbench_tasks(execution_id,workbench_id,generation,run_id,fence,task_uid,resource)
        VALUES(p_execution,w.id,p_generation,p_run,p_fence,uid,resource_value) RETURNING * INTO t;
    UPDATE public.cloud_workbenches SET next_task_uid=uid+1,
        last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
    -- The exact cleanup identity is durable BEFORE any provider command starts.
    UPDATE public.agent_run_executions SET resource=resource_value WHERE id=p_execution AND run_id=p_run AND fence=p_fence;
    RETURN to_jsonb(t);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_acquire_in_session(p_id uuid,p_session uuid,p_workbench uuid,p_generation uuid,
 p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; c public.cloud_workbench_connections; uid integer;
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
 uid:=public.cloud_workbench_task_uid(w);
 INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch,
   access_session_id)
 VALUES(p_id,w.id,w.generation,p_user,p_project,uid,
   w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',uid),w.binding,w.compute_epoch,
   p_session) RETURNING * INTO c;
 UPDATE public.cloud_workbenches SET next_task_uid=uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
 RETURN to_jsonb(c);
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_task_uid(public.cloud_workbenches) FROM PUBLIC,anon,authenticated;
COMMIT;
