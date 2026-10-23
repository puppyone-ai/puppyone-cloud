-- One worker transaction owns claim, initial phase and the current policy snapshot.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

CREATE FUNCTION public.cloud_workbench_begin_open(
 p_workbench uuid,p_generation uuid,p_user uuid,p_project text,p_binding jsonb,p_deadline timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; claimed jsonb; context jsonb;
BEGIN
 IF p_workbench IS NULL OR p_generation IS NULL OR p_user IS NULL OR p_project IS NULL
 OR p_deadline IS NULL THEN
  RAISE EXCEPTION 'workbench_open_claim_invalid' USING ERRCODE='22023';
 END IF;
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
 OR w.owner_deleted_at IS NOT NULL THEN
  RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 IF w.generation IS DISTINCT FROM p_generation OR w.open_deadline IS DISTINCT FROM p_deadline
 OR NOT w.open_requested OR w.open_deadline<=clock_timestamp() THEN
  RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
 END IF;
 claimed := public.cloud_workbench_acquire(p_workbench,p_user,p_project,p_binding);
 IF NOT (claimed->>'acquired')::boolean THEN RETURN claimed; END IF;
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench;
 claimed := public.cloud_workbench_progress(w.id,w.generation,w.operation_id,
  CASE WHEN w.state='allocating' THEN 'allocating' ELSE 'connecting' END);
 -- Facts and revision come from the same canonical snapshot. The application
 -- evaluates AGENT_RUN before using the provider; cached transport is no grant.
 context := public.cloud_workbench_context(p_user,p_project);
 RETURN jsonb_build_object('acquired',true,'workbench',claimed,
  'facts',context->'facts','revision',context->'revision');
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_begin_open(uuid,uuid,uuid,text,jsonb,timestamptz)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_begin_open(uuid,uuid,uuid,text,jsonb,timestamptz)
 TO service_role;
COMMIT;
