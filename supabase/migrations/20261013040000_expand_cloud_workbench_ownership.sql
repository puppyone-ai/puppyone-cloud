-- Expand only. Existing Sessions, Runs and Session-owned resources are unchanged.
-- Installing this control plane does not activate shared execution or retire legacy resources.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE TABLE public.cloud_workbenches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Deliberately no cascading owner FK: provider cleanup must outlive deletion.
    user_id uuid NOT NULL,
    project_id text NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    target_ref text NOT NULL CHECK (target_ref LIKE 'refs/heads/%' AND length(target_ref) BETWEEN 12 AND 1024),
    policy text NOT NULL DEFAULT 'agent-sealed' CHECK (policy IN ('agent-sealed','user-interactive')),
    internet_access boolean NOT NULL DEFAULT false,
    generation uuid NOT NULL DEFAULT gen_random_uuid(),
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    state text NOT NULL DEFAULT 'retired' CHECK (state IN
        ('retired','allocating','running','pausing','paused','resuming','retained','cleanup_pending')),
    binding jsonb NOT NULL DEFAULT '{}',
    resource jsonb,
    operation_id uuid,
    operation_until timestamptz,
    unpublished_changes boolean NOT NULL DEFAULT true,
    last_activity_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    idle_pause_seconds integer NOT NULL DEFAULT 900 CHECK (idle_pause_seconds BETWEEN 60 AND 86400),
    retention_seconds integer NOT NULL DEFAULT 21600 CHECK (retention_seconds BETWEEN 60 AND 2592000),
    owner_deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (resource IS NULL OR jsonb_typeof(resource) = 'object')
);
CREATE INDEX cloud_workbenches_owner ON public.cloud_workbenches(user_id, project_id, created_at);
CREATE INDEX cloud_workbenches_operations ON public.cloud_workbenches(operation_until)
    WHERE state IN ('allocating','pausing','resuming','cleanup_pending');
ALTER TABLE public.cloud_workbenches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cloud_workbenches FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.cloud_workbenches TO service_role;

CREATE FUNCTION public.cloud_workbench_create(p_user uuid, p_project text, p_name text,
    p_target_ref text, p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    -- The canonical AuthorizationService admits the action before this service-only RPC.
    -- The revision fence makes a concurrent revocation reject the write transaction.
    PERFORM public.authorization_assert_revision(p_project, p_revision);
    IF NOT EXISTS (SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready')
        OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id=p_user) THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF p_target_ref !~ '^refs/heads/[^[:space:]~^:?*\[\\]+$'
        OR p_target_ref LIKE '%..%' OR p_target_ref LIKE '%@{%' OR p_target_ref LIKE '%//%'
        OR p_target_ref LIKE '%/' OR p_target_ref LIKE '%.' OR p_target_ref LIKE '%.lock' THEN
        RAISE EXCEPTION 'workbench_ref_invalid';
    END IF;
    INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref)
        VALUES (p_user,p_project,p_name,p_target_ref) RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_acquire(p_workbench uuid, p_user uuid, p_project text, p_binding jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
        OR w.owner_deleted_at IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(p_binding) IS DISTINCT FROM 'object'
        OR coalesce(p_binding->>'provider','') NOT IN ('docker','e2b')
        OR length(coalesce(p_binding->>'artifact',''))=0 THEN
        RAISE EXCEPTION 'workbench_binding_invalid';
    END IF;
    IF w.state<>'retired' AND w.binding IS DISTINCT FROM p_binding THEN
        RAISE EXCEPTION 'workbench_binding_mismatch';
    END IF;
    IF w.state IN ('running','retained','cleanup_pending','pausing')
        OR (w.state IN ('allocating','resuming') AND w.operation_until>clock_timestamp()) THEN
        RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
    END IF;
    IF w.state='retired' AND w.resource IS NOT NULL THEN
        RAISE EXCEPTION 'workbench_cleanup_required';
    END IF;
    UPDATE public.cloud_workbenches SET
        state=CASE WHEN w.state='retired' THEN 'allocating' WHEN w.state='paused' THEN 'resuming' ELSE w.state END,
        generation=CASE WHEN w.state='retired' THEN gen_random_uuid() ELSE generation END,
        binding=p_binding, operation_id=gen_random_uuid(), operation_until=clock_timestamp()+interval '180 seconds',
        version=version+1, updated_at=clock_timestamp()
        WHERE id=p_workbench RETURNING * INTO w;
    -- A timed-out allocation keeps its generation AND exact provider intent.
    -- Reconciliation may resolve that intent; it must not blindly create again.
    RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

CREATE FUNCTION public.cloud_workbench_transition(p_workbench uuid, p_generation uuid,
    p_version bigint, p_operation uuid, p_state text, p_resource jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; resource_value jsonb;
BEGIN
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
        OR w.operation_id IS DISTINCT FROM p_operation OR p_operation IS NULL
        OR w.operation_until IS NULL OR w.operation_until<=clock_timestamp() THEN
        RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000';
    END IF;
    IF NOT (
        (w.state='allocating' AND p_state IN ('allocating','running','retained')) OR
        (w.state='resuming' AND p_state IN ('running','retained')) OR
        (w.state='running' AND p_state IN ('pausing','retained')) OR
        (w.state='pausing' AND p_state IN ('paused','retained')) OR
        (w.state='paused' AND p_state IN ('cleanup_pending','retained')) OR
        (w.state='cleanup_pending' AND p_state IN ('retired','retained'))
    ) THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
    IF p_state IN ('cleanup_pending','retired') AND w.unpublished_changes THEN
        RAISE EXCEPTION 'workbench_unique_copy_protected';
    END IF;
    resource_value := coalesce(p_resource,w.resource);
    IF resource_value IS NOT NULL AND (
        jsonb_typeof(resource_value) IS DISTINCT FROM 'object'
        OR resource_value->>'workbench_id' IS DISTINCT FROM w.id::text
        OR resource_value->>'workbench_generation' IS DISTINCT FROM w.generation::text
        OR resource_value->>'provider' IS DISTINCT FROM w.binding->>'provider'
        OR length(coalesce(resource_value->>'resource_id',''))=0
    ) THEN RAISE EXCEPTION 'workbench_resource_mismatch'; END IF;
    IF w.resource IS NOT NULL AND p_resource IS NOT NULL
        AND coalesce(w.resource->>'allocation_pending','false')<>'true'
        AND (w.resource->>'resource_id' IS DISTINCT FROM p_resource->>'resource_id') THEN
        RAISE EXCEPTION 'workbench_resource_mismatch';
    END IF;
    IF p_state='running' AND (resource_value IS NULL
        OR coalesce(resource_value->>'allocation_pending','true')<>'false') THEN
        RAISE EXCEPTION 'workbench_resource_unconfirmed';
    END IF;
    UPDATE public.cloud_workbenches SET state=p_state,
        resource=CASE WHEN p_state='retired' THEN NULL ELSE resource_value END,
        version=version+1, operation_until=clock_timestamp()+interval '180 seconds',
        updated_at=clock_timestamp()
        WHERE id=p_workbench RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_delete_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF OLD.state<>'retired' OR OLD.resource IS NOT NULL THEN
        RAISE EXCEPTION 'workbench_cleanup_required' USING ERRCODE='55000';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER cloud_workbench_delete_guard BEFORE DELETE ON public.cloud_workbenches
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_delete_guard();

CREATE FUNCTION public.cloud_workbench_owner_deleted() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE public.cloud_workbenches SET owner_deleted_at=clock_timestamp(),
        state=CASE WHEN resource IS NULL AND state='retired' THEN state ELSE 'retained' END,
        version=version+1, operation_id=gen_random_uuid(), updated_at=clock_timestamp()
    WHERE owner_deleted_at IS NULL AND
        ((TG_TABLE_NAME='projects' AND project_id=OLD.id::text) OR
         (TG_TABLE_NAME='users' AND user_id::text=OLD.id::text));
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER cloud_workbench_project_deleting BEFORE UPDATE OF lifecycle_status ON public.projects
    FOR EACH ROW WHEN (NEW.lifecycle_status='deleting' AND OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status)
    EXECUTE FUNCTION public.cloud_workbench_owner_deleted();
CREATE TRIGGER cloud_workbench_project_deleted BEFORE DELETE ON public.projects
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_owner_deleted();
CREATE TRIGGER cloud_workbench_user_deleted BEFORE DELETE ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_owner_deleted();

-- Null is the explicit legacy contract. Never backfill ownership from names or
-- move existing Session resources while old API/worker binaries may still run.
ALTER TABLE public.chat_sessions ADD COLUMN workbench_id uuid REFERENCES public.cloud_workbenches(id);
ALTER TABLE public.agent_runs ADD COLUMN workbench_id uuid REFERENCES public.cloud_workbenches(id);
CREATE INDEX agent_runs_workbench ON public.agent_runs(workbench_id) WHERE workbench_id IS NOT NULL;

CREATE FUNCTION public.cloud_workbench_attach_run() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE selected uuid := (NEW.policy->>'workbench_id')::uuid;
    session_workbench uuid; w public.cloud_workbenches;
BEGIN
    SELECT workbench_id INTO session_workbench FROM public.chat_sessions WHERE id=NEW.session_id FOR UPDATE;
    IF selected IS NULL THEN
        IF session_workbench IS NOT NULL OR NEW.workbench_id IS NOT NULL THEN
            RAISE EXCEPTION 'agent_workbench_required';
        END IF;
        RETURN NEW;
    END IF;
    IF session_workbench IS NOT NULL AND session_workbench<>selected THEN
        RAISE EXCEPTION 'agent_workbench_session_mismatch';
    END IF;
    IF session_workbench IS NULL AND EXISTS(SELECT 1 FROM public.agent_runs WHERE session_id=NEW.session_id) THEN
        RAISE EXCEPTION 'agent_workbench_session_mismatch';
    END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=selected FOR UPDATE;
    IF NOT FOUND OR w.user_id<>NEW.user_id OR w.project_id<>NEW.project_id OR w.owner_deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    UPDATE public.chat_sessions SET workbench_id=selected WHERE id=NEW.session_id;
    NEW.workbench_id := selected;
    RETURN NEW;
END $$;
CREATE TRIGGER cloud_workbench_attach_run BEFORE INSERT ON public.agent_runs
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_attach_run();

CREATE FUNCTION public.cloud_workbench_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME='agent_session_workspaces' THEN
        IF EXISTS (SELECT 1 FROM public.chat_sessions WHERE id=NEW.session_id AND workbench_id IS NOT NULL) THEN
            RAISE EXCEPTION 'agent_workbench_legacy_resource_forbidden';
        END IF;
    ELSIF OLD.workbench_id IS DISTINCT FROM NEW.workbench_id THEN
        IF TG_TABLE_NAME='agent_runs' OR OLD.workbench_id IS NOT NULL
            OR EXISTS (SELECT 1 FROM public.agent_runs WHERE session_id=OLD.id::text) THEN
            RAISE EXCEPTION 'agent_workbench_binding_immutable';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cloud_workbench_run_binding BEFORE UPDATE OF workbench_id ON public.agent_runs
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_binding_guard();
CREATE TRIGGER cloud_workbench_session_binding BEFORE UPDATE OF workbench_id ON public.chat_sessions
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_binding_guard();
CREATE TRIGGER cloud_workbench_legacy_resource BEFORE INSERT OR UPDATE ON public.agent_session_workspaces
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_binding_guard();

-- A worker opts into the shared contract explicitly. The old RPC signature
-- stays valid but cannot hand a new task to a Session-exclusive supervisor.
CREATE FUNCTION public.agent_run_claim_contract(p_worker text,p_ttl integer,p_workbench boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs%ROWTYPE; execution uuid:=gen_random_uuid();
BEGIN
    IF p_ttl IS NULL OR p_ttl NOT BETWEEN 10 AND 120 THEN RAISE EXCEPTION 'agent_lease_invalid'; END IF;
    SELECT * INTO r FROM public.agent_runs WHERE
        (workbench_id IS NOT NULL)=p_workbench AND
        (state IN ('queued','running','waiting_approval','publishing') OR snapshot->>'resource_retained'='true')
        AND (lease_until IS NULL OR lease_until<clock_timestamp())
        ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE public.agent_runs SET execution_id=execution,fence=fence+1,
        lease_until=clock_timestamp()+make_interval(secs=>p_ttl)
        WHERE id=r.id RETURNING * INTO r;
    INSERT INTO public.agent_run_executions(id,run_id,fence,worker_id) VALUES(execution,r.id,r.fence,p_worker);
    PERFORM public.agent_run_event(r.id,'execution',jsonb_build_object('execution_id',execution,'fence',r.fence));
    RETURN to_jsonb(r);
END $$;
CREATE OR REPLACE FUNCTION public.agent_run_claim(p_worker text,p_ttl integer DEFAULT 45)
RETURNS jsonb LANGUAGE sql SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.agent_run_claim_contract(p_worker,p_ttl,false)
$$;
CREATE FUNCTION public.agent_run_claim_workbench_execution(p_worker text)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r jsonb;
BEGIN
    r:=public.agent_run_claim_contract(p_worker,45,true);
    IF r IS NULL THEN RETURN NULL; END IF;
    RETURN public.agent_run_execution_view((r->>'id')::uuid);
END $$;
REVOKE ALL ON FUNCTION public.cloud_workbench_attach_run(), public.cloud_workbench_binding_guard(),
    public.agent_run_claim_contract(text,integer,boolean),
    public.agent_run_claim_workbench_execution(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.agent_run_claim_contract(text,integer,boolean),
    public.agent_run_claim_workbench_execution(text) TO service_role;

REVOKE ALL ON FUNCTION public.cloud_workbench_create(uuid,text,text,text,jsonb),
    public.cloud_workbench_acquire(uuid,uuid,text,jsonb),
    public.cloud_workbench_transition(uuid,uuid,bigint,uuid,text,jsonb),
    public.cloud_workbench_delete_guard(), public.cloud_workbench_owner_deleted()
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_create(uuid,text,text,text,jsonb),
    public.cloud_workbench_acquire(uuid,uuid,text,jsonb),
    public.cloud_workbench_transition(uuid,uuid,bigint,uuid,text,jsonb) TO service_role;
COMMIT;
