-- Expand: durable per-execution process identities, not per-Run computers.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

ALTER TABLE public.cloud_workbenches
    ADD COLUMN next_task_uid integer NOT NULL DEFAULT 20000 CHECK (next_task_uid BETWEEN 20000 AND 60000),
    ADD COLUMN paused_at timestamptz;
CREATE TABLE public.cloud_workbench_tasks (
    execution_id uuid PRIMARY KEY,
    workbench_id uuid NOT NULL REFERENCES public.cloud_workbenches(id),
    generation uuid NOT NULL,
    run_id uuid NOT NULL,
    fence bigint NOT NULL,
    task_uid integer NOT NULL CHECK (task_uid BETWEEN 20000 AND 59999),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','released')),
    resource jsonb NOT NULL CHECK (jsonb_typeof(resource)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    released_at timestamptz,
    UNIQUE(workbench_id,generation,task_uid)
);
CREATE INDEX cloud_workbench_tasks_active ON public.cloud_workbench_tasks(workbench_id,generation,run_id)
    WHERE state='active';
ALTER TABLE public.cloud_workbench_tasks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cloud_workbench_tasks FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.cloud_workbench_tasks TO service_role;

CREATE FUNCTION public.cloud_workbench_has_occupants(p_workbench uuid,p_generation uuid)
RETURNS boolean LANGUAGE sql SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.cloud_workbench_tasks
        WHERE workbench_id=p_workbench AND generation=p_generation AND state='active')
        OR EXISTS(SELECT 1 FROM public.agent_runs WHERE workbench_id=p_workbench
            AND state IN ('queued','running','waiting_approval','publishing'))
$$;

CREATE FUNCTION public.cloud_workbench_task_acquire(p_run uuid,p_execution uuid,p_fence bigint,p_generation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs; w public.cloud_workbenches; t public.cloud_workbench_tasks;
    status jsonb; resource_value jsonb;
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
    IF w.next_task_uid>59999 THEN RAISE EXCEPTION 'workbench_task_identity_exhausted'; END IF;
    resource_value:=w.resource || jsonb_build_object('task_execution_id',p_execution,
        'task_uid',w.next_task_uid,'task_run_id',p_run,'task_fence',p_fence);
    INSERT INTO public.cloud_workbench_tasks(execution_id,workbench_id,generation,run_id,fence,task_uid,resource)
        VALUES(p_execution,w.id,p_generation,p_run,p_fence,w.next_task_uid,resource_value) RETURNING * INTO t;
    UPDATE public.cloud_workbenches SET next_task_uid=next_task_uid+1,
        last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
    -- The exact cleanup identity is durable BEFORE any provider command starts.
    UPDATE public.agent_run_executions SET resource=resource_value WHERE id=p_execution AND run_id=p_run AND fence=p_fence;
    RETURN to_jsonb(t);
END $$;

CREATE FUNCTION public.cloud_workbench_task_released(p_run uuid,p_execution uuid,p_fence bigint,p_task uuid,p_generation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs; w public.cloud_workbenches; t public.cloud_workbench_tasks;
BEGIN
    IF p_run IS NULL OR p_execution IS NULL OR p_fence IS NULL OR p_task IS NULL OR p_generation IS NULL THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    -- Cleanup stays available after cancellation/revocation, but only to the
    -- current fenced owner of that Run, including cleanup of its predecessor.
    r:=public.agent_run_assert(p_run,p_execution,p_fence);
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=r.workbench_id FOR UPDATE;
    SELECT * INTO t FROM public.cloud_workbench_tasks WHERE execution_id=p_task FOR UPDATE;
    IF NOT FOUND OR w.id IS NULL OR t.run_id<>p_run OR t.workbench_id<>w.id
        OR t.generation IS DISTINCT FROM p_generation OR t.fence>p_fence THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    IF t.state='released' THEN RETURN to_jsonb(t); END IF;
    IF w.generation IS DISTINCT FROM p_generation THEN RAISE EXCEPTION 'workbench_fenced'; END IF;
    -- This ACK means that task's OS processes were confirmed dead, not merely
    -- that its lease elapsed. Never touches provider pause/delete or other tasks.
    UPDATE public.cloud_workbench_tasks SET state='released',released_at=clock_timestamp()
        WHERE execution_id=p_task RETURNING * INTO t;
    UPDATE public.agent_run_executions SET cleaned=true WHERE id=p_task AND run_id=p_run;
    UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() WHERE id=w.id;
    RETURN to_jsonb(t);
END $$;

CREATE FUNCTION public.cloud_workbench_occupancy_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.generation IS DISTINCT FROM OLD.generation THEN
        IF OLD.state<>'retired' OR OLD.resource IS NOT NULL
            OR EXISTS(SELECT 1 FROM public.cloud_workbench_tasks
                WHERE workbench_id=OLD.id AND generation=OLD.generation AND state='active') THEN
            RAISE EXCEPTION 'workbench_generation_in_use';
        END IF;
        NEW.next_task_uid:=20000;
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND (
        (NEW.state IN ('pausing','cleanup_pending') AND public.cloud_workbench_has_occupants(OLD.id,OLD.generation))
        OR (NEW.state='retired' AND EXISTS(SELECT 1 FROM public.cloud_workbench_tasks
            WHERE workbench_id=OLD.id AND generation=OLD.generation AND state='active'))
    ) THEN RAISE EXCEPTION 'workbench_has_active_occupants'; END IF;
    -- Once cleanup was claimed, a new queued Run waits for its ACK. It cannot
    -- prevent retirement of the old generation and strand an already-deleted VM.
    IF NEW.state='paused' AND OLD.state<>'paused' THEN NEW.paused_at:=clock_timestamp();
    ELSIF NEW.state IN ('allocating','running') THEN NEW.paused_at:=NULL; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cloud_workbench_occupancy_guard BEFORE UPDATE OF state,generation ON public.cloud_workbenches
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_occupancy_guard();

CREATE FUNCTION public.cloud_workbench_idle_claim(p_workbench uuid,p_generation uuid,p_version bigint,p_action text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    IF p_action IS NULL OR p_action NOT IN ('pause','retire') THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('acquired',false,'workbench',NULL); END IF;
    IF w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
        OR w.owner_deleted_at IS NOT NULL OR public.cloud_workbench_has_occupants(w.id,w.generation)
        OR (p_action='pause' AND (w.state<>'running'
            OR w.last_activity_at+make_interval(secs=>w.idle_pause_seconds)>clock_timestamp()))
        OR (p_action='retire' AND (w.state<>'paused' OR w.unpublished_changes OR w.paused_at IS NULL
            OR w.paused_at+make_interval(secs=>w.retention_seconds)>clock_timestamp())) THEN
        RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
    END IF;
    UPDATE public.cloud_workbenches SET state=CASE WHEN p_action='pause' THEN 'pausing' ELSE 'cleanup_pending' END,
        version=version+1,operation_id=gen_random_uuid(),operation_until=clock_timestamp()+interval '180 seconds',
        updated_at=clock_timestamp() WHERE id=w.id RETURNING * INTO w;
    RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

CREATE FUNCTION public.cloud_workbench_task_delete_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME='cloud_workbench_tasks' THEN
        IF OLD.state='active' THEN RAISE EXCEPTION 'workbench_task_cleanup_required'; END IF;
    ELSIF EXISTS(SELECT 1 FROM public.cloud_workbench_tasks WHERE run_id=OLD.id AND state='active') THEN
        RAISE EXCEPTION 'workbench_task_cleanup_required';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER cloud_workbench_task_delete_guard BEFORE DELETE ON public.cloud_workbench_tasks
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_task_delete_guard();
CREATE TRIGGER cloud_workbench_run_delete_guard BEFORE DELETE ON public.agent_runs
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_task_delete_guard();

REVOKE ALL ON FUNCTION public.cloud_workbench_has_occupants(uuid,uuid),
    public.cloud_workbench_task_acquire(uuid,uuid,bigint,uuid),
    public.cloud_workbench_task_released(uuid,uuid,bigint,uuid,uuid),
    public.cloud_workbench_occupancy_guard(), public.cloud_workbench_idle_claim(uuid,uuid,bigint,text),
    public.cloud_workbench_task_delete_guard() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_task_acquire(uuid,uuid,bigint,uuid),
    public.cloud_workbench_task_released(uuid,uuid,bigint,uuid,uuid),
    public.cloud_workbench_idle_claim(uuid,uuid,bigint,text) TO service_role;
COMMIT;
