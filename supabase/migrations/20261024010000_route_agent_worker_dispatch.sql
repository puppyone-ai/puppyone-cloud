-- Expand only. Work is routed to the worker pool of the deployment that admitted
-- it, and to workers that implement the Run's runtime contract. This covers
-- every claim a worker makes: Runs, workspace open requests, workspace
-- maintenance and Session workspace retirement.
--
-- Compatibility: every existing RPC keeps its signature. Old API binaries create
-- work in pool 'default' with contract 1; old worker binaries claim only that.
-- A pool other than 'default' is therefore never claimed by an old worker.
--
-- A lease is ownership only. dispatch_after is the earliest time a Run may be
-- claimed again, so a released or finished execution is fenced immediately
-- while its successor can still be deferred.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

ALTER TABLE public.agent_runs
    ADD COLUMN worker_pool text NOT NULL DEFAULT 'default'
        CHECK (worker_pool ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    ADD COLUMN runtime_contract integer NOT NULL DEFAULT 1
        CHECK (runtime_contract BETWEEN 1 AND 1000),
    ADD COLUMN dispatch_after timestamptz,
    -- Releases by workers whose configuration cannot serve this Run.
    ADD COLUMN dispatch_attempts integer NOT NULL DEFAULT 0 CHECK (dispatch_attempts >= 0),
    -- Terminal finishes that still retained provider resources.
    ADD COLUMN cleanup_attempts integer NOT NULL DEFAULT 0 CHECK (cleanup_attempts >= 0);
CREATE INDEX agent_runs_pool_dispatch ON public.agent_runs(worker_pool, created_at)
    WHERE state IN ('queued','running','waiting_approval','publishing')
        OR snapshot->>'resource_retained'='true';

ALTER TABLE public.agent_run_executions
    -- Who executed the attempt: id, pool, contracts, service, environment, host, build.
    ADD COLUMN worker jsonb CHECK (worker IS NULL OR jsonb_typeof(worker)='object'),
    ADD COLUMN released_at timestamptz,
    ADD COLUMN release_reason text CHECK (release_reason IN ('worker_shutdown','worker_incapable'));

-- The pool that last requested this workbench open executes the request and
-- maintains the running workbench.
ALTER TABLE public.cloud_workbenches
    ADD COLUMN worker_pool text NOT NULL DEFAULT 'default'
        CHECK (worker_pool ~ '^[a-z0-9][a-z0-9-]{0,62}$');

-- The admitting API states the route in the frozen Run policy, the same
-- channel already used for workbench_id. The columns are the only authority;
-- the routing request is removed from the stored policy.
CREATE FUNCTION public.agent_run_dispatch_bind() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE route jsonb := NEW.policy->'dispatch';
BEGIN
    IF route IS NULL THEN RETURN NEW; END IF;
    IF jsonb_typeof(route) IS DISTINCT FROM 'object'
        OR jsonb_typeof(route->'pool') IS DISTINCT FROM 'string'
        OR jsonb_typeof(route->'contract') IS DISTINCT FROM 'number'
        OR coalesce(route->>'contract','') !~ '^[0-9]{1,4}$' THEN
        RAISE EXCEPTION 'agent_dispatch_invalid' USING ERRCODE='22023';
    END IF;
    NEW.worker_pool := route->>'pool';
    NEW.runtime_contract := (route->>'contract')::integer;
    NEW.policy := NEW.policy - 'dispatch';
    RETURN NEW;
END $$;
CREATE TRIGGER agent_run_dispatch_bind BEFORE INSERT ON public.agent_runs
    FOR EACH ROW EXECUTE FUNCTION public.agent_run_dispatch_bind();

CREATE FUNCTION public.agent_run_claim_routed(p_worker jsonb,p_ttl integer,p_workbench boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs%ROWTYPE; v_execution uuid:=gen_random_uuid();
    v_worker text; v_pool text; v_lowest integer; v_highest integer;
BEGIN
    IF p_ttl IS NULL OR p_ttl NOT BETWEEN 10 AND 120 THEN RAISE EXCEPTION 'agent_lease_invalid'; END IF;
    IF jsonb_typeof(p_worker) IS DISTINCT FROM 'object' OR octet_length(p_worker::text)>4096
        OR jsonb_typeof(p_worker->'id') IS DISTINCT FROM 'string'
        OR length(p_worker->>'id') NOT BETWEEN 1 AND 200
        OR coalesce(p_worker->>'pool','') !~ '^[a-z0-9][a-z0-9-]{0,62}$'
        OR jsonb_typeof(p_worker->'contracts') IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_worker->'contracts')<>2
        OR coalesce(p_worker->'contracts'->>0,'') !~ '^[0-9]{1,4}$'
        OR coalesce(p_worker->'contracts'->>1,'') !~ '^[0-9]{1,4}$' THEN
        RAISE EXCEPTION 'agent_worker_invalid' USING ERRCODE='22023';
    END IF;
    v_worker:=p_worker->>'id';
    v_pool:=p_worker->>'pool';
    v_lowest:=(p_worker->'contracts'->>0)::integer;
    v_highest:=(p_worker->'contracts'->>1)::integer;
    IF v_lowest<1 OR v_highest<v_lowest THEN RAISE EXCEPTION 'agent_worker_invalid' USING ERRCODE='22023'; END IF;
    SELECT * INTO r FROM public.agent_runs a WHERE a.worker_pool=v_pool
        AND a.runtime_contract BETWEEN v_lowest AND v_highest
        AND (a.workbench_id IS NOT NULL)=p_workbench
        AND (a.state IN ('queued','running','waiting_approval','publishing') OR a.snapshot->>'resource_retained'='true')
        AND (a.lease_until IS NULL OR a.lease_until<clock_timestamp())
        AND (a.dispatch_after IS NULL OR a.dispatch_after<=clock_timestamp())
        -- A worker never takes back a Run it could not serve; a peer can.
        AND NOT EXISTS(SELECT 1 FROM public.agent_run_executions e WHERE e.run_id=a.id
            AND e.worker_id=v_worker AND e.release_reason='worker_incapable')
        ORDER BY a.created_at FOR UPDATE OF a SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE public.agent_runs SET execution_id=v_execution,fence=fence+1,dispatch_after=NULL,
        lease_until=clock_timestamp()+make_interval(secs=>p_ttl)
        WHERE id=r.id RETURNING * INTO r;
    INSERT INTO public.agent_run_executions(id,run_id,fence,worker_id,worker)
        VALUES(v_execution,r.id,r.fence,v_worker,p_worker);
    -- Run events are user-visible: name the worker without host or environment.
    PERFORM public.agent_run_event(r.id,'execution',jsonb_build_object('execution_id',v_execution,'fence',r.fence,
        'worker',jsonb_build_object('id',v_worker,'pool',v_pool,'build',p_worker->'build')));
    RETURN to_jsonb(r);
END $$;

-- Deployed workers call these signatures without routing information.
CREATE OR REPLACE FUNCTION public.agent_run_claim_contract(p_worker text,p_ttl integer,p_workbench boolean)
RETURNS jsonb LANGUAGE sql SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.agent_run_claim_routed(
        jsonb_build_object('id',p_worker,'pool','default','contracts',jsonb_build_array(1,1)),
        p_ttl,p_workbench)
$$;

CREATE FUNCTION public.agent_run_dispatch(p_worker jsonb,p_workbench boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r jsonb;
BEGIN
    r:=public.agent_run_claim_routed(p_worker,45,p_workbench);
    IF r IS NULL THEN RETURN NULL; END IF;
    RETURN public.agent_run_execution_view((r->>'id')::uuid);
END $$;

-- Return a claimed Run to its pool's queue. Equivalent to lease expiry without
-- the wait: the releasing execution is fenced at once and the successor's
-- takeover rules apply unchanged.
CREATE FUNCTION public.agent_run_release(p_run uuid,p_execution uuid,p_fence bigint,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs%ROWTYPE;
BEGIN
    IF p_reason IS NULL OR p_reason NOT IN ('worker_shutdown','worker_incapable') THEN
        RAISE EXCEPTION 'agent_release_invalid' USING ERRCODE='22023';
    END IF;
    PERFORM public.agent_run_assert(p_run,p_execution,p_fence);
    UPDATE public.agent_runs SET lease_until=clock_timestamp(),
        dispatch_attempts=dispatch_attempts+CASE WHEN p_reason='worker_incapable' THEN 1 ELSE 0 END
        WHERE id=p_run RETURNING * INTO r;
    UPDATE public.agent_run_executions SET released_at=clock_timestamp(),release_reason=p_reason
        WHERE id=p_execution;
    PERFORM public.agent_run_event(p_run,'execution_released',
        jsonb_build_object('execution_id',p_execution,'fence',p_fence,'reason',p_reason));
    RETURN to_jsonb(r);
END $$;

-- Terminal events name the executing worker. Retained provider resources are
-- retried with exponential backoff instead of a fixed 45 second lease loop, and
-- the finishing execution is fenced immediately.
CREATE OR REPLACE FUNCTION public.agent_run_finish(p_run uuid,p_execution uuid,p_fence bigint,
 p_state text,p_code text,p_snapshot jsonb,p_publication jsonb,p_cleaned boolean)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs%ROWTYPE; result jsonb; retained boolean; v_worker jsonb;
BEGIN
 r:=public.agent_run_assert(p_run,p_execution,p_fence);
 retained:=coalesce((p_snapshot->>'resource_retained')::boolean,false);
 SELECT CASE WHEN e.worker IS NULL THEN NULL ELSE
   jsonb_build_object('id',e.worker->'id','pool',e.worker->'pool','build',e.worker->'build') END
   INTO v_worker FROM public.agent_run_executions e WHERE e.id=p_execution;
 IF coalesce(p_publication,'null'::jsonb) IS DISTINCT FROM coalesce(r.publication,'null'::jsonb) THEN
   PERFORM public.agent_run_write(p_run,p_execution,p_fence,'publication',p_publication,
     jsonb_build_object('publication',p_publication));
 END IF;
 result:=public.agent_run_write(p_run,p_execution,p_fence,'terminal',
   jsonb_build_object('state',p_state,'code',p_code,'resource_retained',retained)
     || CASE WHEN v_worker IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('worker',v_worker) END,
   jsonb_build_object('state',p_state,'snapshot',p_snapshot) ||
     CASE WHEN p_cleaned THEN '{"cleaned":true}'::jsonb ELSE '{}'::jsonb END);
 UPDATE public.agent_session_workspaces SET idle_since=coalesce(idle_since,clock_timestamp()),
   retire_after=coalesce(retire_after,clock_timestamp()+interval '6 hours'),updated_at=clock_timestamp()
 WHERE session_id=r.session_id AND execution_id=p_execution AND fence=p_fence AND state='paused';
 IF retained THEN
   UPDATE public.agent_runs SET cleanup_attempts=cleanup_attempts+1,lease_until=clock_timestamp(),
     dispatch_after=clock_timestamp()+make_interval(secs=>least(45*power(2,least(cleanup_attempts,10)),3600))
     WHERE id=p_run RETURNING * INTO r;
   result:=to_jsonb(r);
 END IF;
 RETURN result;
END $$;

-- A Session workspace is retired by the pool of the Run that last acquired it;
-- receipts without a Run belong to the deployed default.
CREATE FUNCTION public.agent_run_workspace_due_in_pool(p_pool text,p_limit integer DEFAULT 20)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 IF p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'agent_workspace_batch_invalid'; END IF;
 IF coalesce(p_pool,'') !~ '^[a-z0-9][a-z0-9-]{0,62}$' THEN
   RAISE EXCEPTION 'agent_worker_invalid' USING ERRCODE='22023';
 END IF;
 WITH due AS (
   SELECT w.session_id FROM public.agent_session_workspaces w
   WHERE coalesce((SELECT o.worker_pool FROM public.agent_runs o WHERE o.id=w.run_id),'default')=p_pool
     AND ((w.state='paused' AND w.retire_after<=clock_timestamp()
     AND NOT EXISTS(SELECT 1 FROM public.agent_runs r WHERE r.session_id=w.session_id
      AND r.state IN ('queued','running','waiting_approval','publishing')))
     OR (w.state='cleanup_pending' AND w.operation_until<=clock_timestamp()))
   ORDER BY coalesce(w.retire_after,w.operation_until) LIMIT p_limit FOR UPDATE OF w SKIP LOCKED
 ), claimed AS (
   UPDATE public.agent_session_workspaces w SET state='cleanup_pending',version=version+1,
      operation_id=gen_random_uuid(),operation_until=clock_timestamp()+interval '2 minutes',updated_at=clock_timestamp()
   FROM due WHERE w.session_id=due.session_id RETURNING w.*
 ) SELECT coalesce(jsonb_agg(to_jsonb(claimed)),'[]'::jsonb) INTO result FROM claimed;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.agent_run_workspace_due(p_limit integer DEFAULT 20)
RETURNS jsonb LANGUAGE sql SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT public.agent_run_workspace_due_in_pool('default',p_limit)
$$;

CREATE FUNCTION public.cloud_workbench_request_open_in_pool(p_workbench uuid,p_user uuid,p_project text,
 p_binding jsonb,p_revision jsonb,p_pool text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 IF coalesce(p_pool,'') !~ '^[a-z0-9][a-z0-9-]{0,62}$' THEN
  RAISE EXCEPTION 'workbench_pool_invalid' USING ERRCODE='22023';
 END IF;
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
 OR w.owner_deleted_at IS NOT NULL OR NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready') THEN
  RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 IF jsonb_typeof(p_binding) IS DISTINCT FROM 'object' OR coalesce(p_binding->>'provider','') !~ '^[a-z][a-z0-9_-]{0,62}$'
 OR length(coalesce(p_binding->>'artifact',''))=0 THEN RAISE EXCEPTION 'workbench_binding_invalid'; END IF;
 IF w.state<>'retired' AND w.binding IS DISTINCT FROM p_binding THEN RAISE EXCEPTION 'workbench_binding_mismatch'; END IF;
 IF w.state IN ('retained','cleanup_pending') THEN RETURN to_jsonb(w); END IF;
 -- Repeated clicks never extend an operation's deadline, identity or owning pool.
 IF (w.open_requested AND (w.open_deadline>clock_timestamp() OR w.operation_until>clock_timestamp())) OR w.state='running' THEN RETURN to_jsonb(w); END IF;
 UPDATE public.cloud_workbenches SET open_requested=true,open_deadline=clock_timestamp()+interval '10 minutes',
 binding=p_binding,worker_pool=p_pool,operation_phase=CASE WHEN state='pausing' THEN 'pausing' ELSE 'queued' END,
 operation_error=NULL,operation_retryable=false,updated_at=clock_timestamp()
 WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_request_open(p_workbench uuid,p_user uuid,p_project text,p_binding jsonb,p_revision jsonb)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT public.cloud_workbench_request_open_in_pool(p_workbench,p_user,p_project,p_binding,p_revision,'default')
$$;

CREATE FUNCTION public.cloud_workbench_pending_in_pool(p_provider text,p_pool text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT * FROM public.cloud_workbenches WHERE open_requested AND binding->>'provider'=p_provider
 AND worker_pool=p_pool
 AND (open_deadline<=clock_timestamp() OR (state<>'pausing'
   AND (state NOT IN ('allocating','resuming') OR operation_until IS NULL OR operation_until<=clock_timestamp())))
 ORDER BY updated_at LIMIT least(greatest(p_limit,1),100)
$$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_pending(p_provider text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT * FROM public.cloud_workbench_pending_in_pool(p_provider,'default',p_limit)
$$;

CREATE FUNCTION public.cloud_workbench_maintenance_in_pool(p_provider text,p_pool text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH due AS (SELECT id FROM public.cloud_workbenches WHERE state<>'retired' AND binding->>'provider'=p_provider
 AND worker_pool=p_pool
 AND maintenance_after<=clock_timestamp() ORDER BY maintenance_after FOR UPDATE SKIP LOCKED
 LIMIT least(greatest(p_limit,1),100))
 UPDATE public.cloud_workbenches w SET maintenance_after=clock_timestamp()+interval '30 seconds'
 FROM due WHERE w.id=due.id RETURNING w.*
$$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_maintenance_for_provider(p_provider text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT * FROM public.cloud_workbench_maintenance_in_pool(p_provider,'default',p_limit)
$$;

REVOKE ALL ON FUNCTION public.agent_run_dispatch_bind(),
    public.agent_run_claim_routed(jsonb,integer,boolean),
    public.agent_run_dispatch(jsonb,boolean),
    public.agent_run_release(uuid,uuid,bigint,text),
    public.agent_run_workspace_due_in_pool(text,integer),
    public.cloud_workbench_request_open_in_pool(uuid,uuid,text,jsonb,jsonb,text),
    public.cloud_workbench_pending_in_pool(text,text,integer),
    public.cloud_workbench_maintenance_in_pool(text,text,integer)
    FROM PUBLIC,anon,authenticated;
-- Every agent_run_* function is backend-only and backend-executable
-- (supabase/tests/_support/cloud_agent_contracts.inc).
GRANT EXECUTE ON FUNCTION public.agent_run_dispatch_bind(),
    public.agent_run_claim_routed(jsonb,integer,boolean),
    public.agent_run_dispatch(jsonb,boolean),
    public.agent_run_release(uuid,uuid,bigint,text),
    public.agent_run_workspace_due_in_pool(text,integer),
    public.cloud_workbench_request_open_in_pool(uuid,uuid,text,jsonb,jsonb,text),
    public.cloud_workbench_pending_in_pool(text,text,integer),
    public.cloud_workbench_maintenance_in_pool(text,text,integer)
    TO service_role;
COMMIT;
