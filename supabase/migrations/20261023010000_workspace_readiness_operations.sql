-- Durable user intent is independent of HTTP and provider connection lifetimes.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';
ALTER TABLE public.cloud_workbenches
 ADD COLUMN open_requested boolean NOT NULL DEFAULT false,
 ADD COLUMN open_deadline timestamptz,
 ADD COLUMN operation_phase text,
 ADD COLUMN operation_error text,
 ADD COLUMN operation_retryable boolean NOT NULL DEFAULT false;
CREATE INDEX cloud_workbenches_open_pending ON public.cloud_workbenches(updated_at)
 WHERE open_requested;

-- Provider registration belongs to the application, not a SQL vendor allowlist.
CREATE OR REPLACE FUNCTION public.cloud_workbench_acquire(p_workbench uuid, p_user uuid, p_project text, p_binding jsonb)
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
        OR coalesce(p_binding->>'provider','') !~ '^[a-z][a-z0-9_-]{0,62}$'
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
        compute_epoch=CASE WHEN w.state IN ('retired','paused') THEN compute_epoch+1 ELSE compute_epoch END,
        binding=p_binding, operation_id=gen_random_uuid(), operation_until=clock_timestamp()+interval '180 seconds',
        version=version+1, updated_at=clock_timestamp()
        WHERE id=p_workbench RETURNING * INTO w;
    -- A timed-out allocation keeps its generation AND exact provider intent.
    -- Reconciliation may resolve that intent; it must not blindly create again.
    RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;


CREATE FUNCTION public.cloud_workbench_request_open(p_workbench uuid,p_user uuid,p_project text,p_binding jsonb,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
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
 -- Repeated clicks never extend an operation's deadline or replace its identity.
 IF (w.open_requested AND (w.open_deadline>clock_timestamp() OR w.operation_until>clock_timestamp())) OR w.state='running' THEN RETURN to_jsonb(w); END IF;
 UPDATE public.cloud_workbenches SET open_requested=true,open_deadline=clock_timestamp()+interval '10 minutes',
 binding=p_binding,operation_phase=CASE WHEN state='pausing' THEN 'pausing' ELSE 'queued' END,
 operation_error=NULL,operation_retryable=false,updated_at=clock_timestamp()
 WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_pending(p_provider text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT * FROM public.cloud_workbenches WHERE open_requested AND binding->>'provider'=p_provider
 AND (open_deadline<=clock_timestamp() OR (state<>'pausing'
   AND (state NOT IN ('allocating','resuming') OR operation_until IS NULL OR operation_until<=clock_timestamp())))
 ORDER BY updated_at LIMIT least(greatest(p_limit,1),100)
$$;

CREATE FUNCTION public.cloud_workbench_progress(p_workbench uuid,p_generation uuid,p_operation uuid,p_phase text,
 p_error text DEFAULT NULL,p_retryable boolean DEFAULT false)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.operation_id IS DISTINCT FROM p_operation
 OR p_operation IS NULL OR w.operation_until IS NULL OR w.operation_until<=clock_timestamp() THEN RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000'; END IF;
 IF p_phase NOT IN ('allocating','connecting','initializing','checking_files','ready','pausing','paused','failed')
 OR (p_error IS NOT NULL AND p_error !~ '^workbench_[a-z_]{1,80}$') THEN RAISE EXCEPTION 'workbench_progress_invalid'; END IF;
 UPDATE public.cloud_workbenches SET operation_phase=p_phase,operation_error=p_error,operation_retryable=p_retryable,
 open_requested=CASE WHEN p_phase IN ('ready','failed') THEN false ELSE open_requested END,
 operation_until=CASE WHEN p_phase='failed' THEN clock_timestamp() ELSE clock_timestamp()+interval '180 seconds' END,
 last_activity_at=CASE WHEN p_phase='ready' THEN clock_timestamp() ELSE last_activity_at END,
 updated_at=clock_timestamp() WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_ready(p_workbench uuid,p_generation uuid,p_version bigint,p_operation uuid,p_resource jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 -- Resource state and user-visible readiness publish in one fenced transaction.
 PERFORM public.cloud_workbench_transition(p_workbench,p_generation,p_version,p_operation,'running',p_resource);
 RETURN public.cloud_workbench_progress(p_workbench,p_generation,p_operation,'ready');
END $$;

CREATE FUNCTION public.cloud_workbench_expire_open(p_workbench uuid,p_deadline timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF w.open_requested AND w.open_deadline IS NOT DISTINCT FROM p_deadline AND w.open_deadline<=clock_timestamp() THEN
  UPDATE public.cloud_workbenches SET open_requested=false,operation_phase='failed',
  operation_error='workbench_start_timeout',operation_retryable=true,operation_until=clock_timestamp(),updated_at=clock_timestamp()
  WHERE id=w.id RETURNING * INTO w;
 END IF;
 RETURN to_jsonb(w);
END $$;

-- Each configured worker maintains only its provider; disabled adapters cannot
-- compete for another deployment's lifecycle claims.
CREATE FUNCTION public.cloud_workbench_maintenance_for_provider(p_provider text,p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH due AS (SELECT id FROM public.cloud_workbenches WHERE state<>'retired' AND binding->>'provider'=p_provider
 AND maintenance_after<=clock_timestamp() ORDER BY maintenance_after FOR UPDATE SKIP LOCKED
 LIMIT least(greatest(p_limit,1),100))
 UPDATE public.cloud_workbenches w SET maintenance_after=clock_timestamp()+interval '30 seconds'
 FROM due WHERE w.id=due.id RETURNING w.*
$$;
REVOKE ALL ON FUNCTION public.cloud_workbench_request_open(uuid,uuid,text,jsonb,jsonb),
 public.cloud_workbench_pending(text,integer),public.cloud_workbench_progress(uuid,uuid,uuid,text,text,boolean),
 public.cloud_workbench_ready(uuid,uuid,bigint,uuid,jsonb),public.cloud_workbench_expire_open(uuid,timestamptz),public.cloud_workbench_maintenance_for_provider(text,integer)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_request_open(uuid,uuid,text,jsonb,jsonb),
 public.cloud_workbench_pending(text,integer),public.cloud_workbench_progress(uuid,uuid,uuid,text,text,boolean),
 public.cloud_workbench_ready(uuid,uuid,bigint,uuid,jsonb),public.cloud_workbench_expire_open(uuid,timestamptz),public.cloud_workbench_maintenance_for_provider(text,integer)
 TO service_role;
COMMIT;
