-- The consuming file connection completes the worker's durable handoff.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE FUNCTION public.cloud_workbench_finish_open(p_workbench uuid,p_generation uuid,
 p_version bigint,p_operation uuid,p_epoch bigint,p_resource jsonb,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 -- Keep authorization revision lock order consistent with activity admission.
 PERFORM public.authorization_assert_revision(p_revision->>'project',p_revision);
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.project_id IS DISTINCT FROM p_revision->>'project'
  OR w.generation IS DISTINCT FROM p_generation OR w.operation_id IS DISTINCT FROM p_operation
  OR p_operation IS NULL OR w.compute_epoch IS DISTINCT FROM p_epoch
  OR w.resource IS DISTINCT FROM p_resource OR w.owner_deleted_at IS NOT NULL THEN
  RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000';
 END IF;
 -- Two authorized consumers may finish the same verified opening concurrently.
 IF w.state='running' AND NOT w.open_requested AND w.operation_phase='ready' THEN RETURN to_jsonb(w); END IF;
 IF w.state NOT IN ('allocating','resuming') OR NOT w.open_requested
  OR w.operation_phase IS DISTINCT FROM 'checking_files' OR w.operation_error IS NOT NULL
  OR w.open_deadline IS NULL OR w.open_deadline<=clock_timestamp() THEN
  RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000';
 END IF;
 RETURN public.cloud_workbench_ready(p_workbench,p_generation,p_version,p_operation,p_resource);
END $$;
REVOKE ALL ON FUNCTION public.cloud_workbench_finish_open(uuid,uuid,bigint,uuid,bigint,jsonb,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_finish_open(uuid,uuid,bigint,uuid,bigint,jsonb,jsonb) TO service_role;
COMMIT;
