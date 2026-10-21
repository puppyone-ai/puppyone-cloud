-- Complete private interactive workspace control without adopting legacy Sessions.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';
ALTER TABLE public.cloud_workbenches
    ADD COLUMN is_default boolean NOT NULL DEFAULT false,
    ADD COLUMN compute_epoch bigint NOT NULL DEFAULT 0,
    ADD COLUMN maintenance_after timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE UNIQUE INDEX cloud_workbenches_default ON public.cloud_workbenches(user_id,project_id)
    WHERE is_default AND owner_deleted_at IS NULL;
CREATE INDEX cloud_workbenches_maintenance ON public.cloud_workbenches(maintenance_after)
    WHERE state<>'retired';

CREATE FUNCTION public.cloud_workbench_context(p_user uuid,p_project text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT jsonb_build_object('facts',public.authorization_project_facts(p_project,p_user),
   'revision',public.authorization_revision_token(p_project),
   'workbench',(SELECT to_jsonb(w) FROM public.cloud_workbenches w WHERE w.user_id=p_user
       AND w.project_id=p_project AND w.is_default AND w.owner_deleted_at IS NULL))
$$;

CREATE FUNCTION public.cloud_workbench_default(p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready')
   OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,is_default)
 VALUES(p_user,p_project,'Workspace','refs/heads/workspaces/'||p_user::text,true)
 ON CONFLICT(user_id,project_id) WHERE is_default AND owner_deleted_at IS NULL DO NOTHING;
 SELECT * INTO w FROM public.cloud_workbenches WHERE user_id=p_user AND project_id=p_project
   AND is_default AND owner_deleted_at IS NULL;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_automation(p_user uuid,p_project text,p_request uuid,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready')
   OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 INSERT INTO public.cloud_workbenches(id,user_id,project_id,name,target_ref)
 VALUES(p_request,p_user,p_project,'Automation','refs/heads/automations/'||p_request::text)
 ON CONFLICT(id) DO NOTHING;
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_request;
 IF w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
   OR w.is_default OR w.owner_deleted_at IS NOT NULL
   OR w.target_ref IS DISTINCT FROM 'refs/heads/automations/'||p_request::text THEN
   RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_activity(p_workbench uuid,p_generation uuid,
 p_user uuid,p_project text,p_revision jsonb,p_write boolean DEFAULT false)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
   OR w.generation IS DISTINCT FROM p_generation OR w.owner_deleted_at IS NOT NULL THEN
   RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 IF w.state<>'running' THEN RAISE EXCEPTION 'workbench_not_running' USING ERRCODE='55000'; END IF;
 UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp(),
    unpublished_changes=unpublished_changes OR p_write WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_maintenance(p_limit integer DEFAULT 20)
RETURNS SETOF public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH due AS (SELECT id FROM public.cloud_workbenches WHERE state<>'retired'
   AND maintenance_after<=clock_timestamp() ORDER BY maintenance_after
   FOR UPDATE SKIP LOCKED LIMIT least(greatest(p_limit,1),100))
 UPDATE public.cloud_workbenches w SET maintenance_after=clock_timestamp()+interval '30 seconds'
 FROM due WHERE w.id=due.id RETURNING w.*
$$;

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
        OR coalesce(p_binding->>'provider','') NOT IN ('docker','e2b','fly')
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

-- Take over only expired lifecycle work. Preserve identity and allocation intent.
CREATE FUNCTION public.cloud_workbench_reconcile_claim(p_workbench uuid,p_generation uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
   OR w.state NOT IN ('allocating','resuming','pausing','retained')
   OR w.operation_until>clock_timestamp()
   OR EXISTS(SELECT 1 FROM public.cloud_workbench_tasks WHERE workbench_id=w.id AND generation=w.generation AND state='active') THEN
   RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
 END IF;
 UPDATE public.cloud_workbenches SET version=version+1,operation_id=gen_random_uuid(),
   operation_until=clock_timestamp()+interval '180 seconds',updated_at=clock_timestamp()
   WHERE id=w.id RETURNING * INTO w;
 RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

-- Billing/auth failure can pause an idle resource without waiting for the idle
-- timer. Active task identities remain protected until their own stop ACK.
CREATE FUNCTION public.cloud_workbench_suspend(p_workbench uuid,p_generation uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
   OR w.state<>'running' OR public.cloud_workbench_has_occupants(w.id,w.generation) THEN
   RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
 END IF;
 UPDATE public.cloud_workbenches SET state='pausing',version=version+1,operation_id=gen_random_uuid(),
   operation_until=clock_timestamp()+interval '180 seconds',updated_at=clock_timestamp()
   WHERE id=w.id RETURNING * INTO w;
 RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;
REVOKE ALL ON FUNCTION public.cloud_workbench_reconcile_claim(uuid,uuid,bigint),
 public.cloud_workbench_suspend(uuid,uuid,bigint) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_reconcile_claim(uuid,uuid,bigint),
 public.cloud_workbench_suspend(uuid,uuid,bigint) TO service_role;

REVOKE ALL ON FUNCTION public.cloud_workbench_context(uuid,text),
 public.cloud_workbench_default(uuid,text,jsonb),
 public.cloud_workbench_automation(uuid,text,uuid,jsonb),
 public.cloud_workbench_activity(uuid,uuid,uuid,text,jsonb,boolean),
 public.cloud_workbench_maintenance(integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_context(uuid,text),
 public.cloud_workbench_default(uuid,text,jsonb),
 public.cloud_workbench_automation(uuid,text,uuid,jsonb),
 public.cloud_workbench_activity(uuid,uuid,uuid,text,jsonb,boolean),
 public.cloud_workbench_maintenance(integer) TO service_role;
COMMIT;
