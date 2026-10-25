-- Additive: compute is replaceable, the disk is the workspace, and deletion means deletion.
-- 1. A resuming workspace whose compute was lost may record a new allocation intent on
--    the same disk (resuming -> resuming), so the sandbox module can recreate compute.
-- 2. When the provider confirms compute AND disk are gone, the generation retires; the
--    next open allocates a new generation from the published state.
-- 3. A workspace whose owner (project or user) was deleted is claimed for destruction by
--    the sandbox module once nothing occupies it. No restore path exists for deleted
--    projects, so the unique-copy protection does not apply to a deleted owner.
-- 4. Deleting an owner no longer fails while an SSH connection is open: marking the
--    owner deleted keeps the generation and every occupant row; the session's next
--    lease renewal fails and reclamation waits until the connection is released.
-- Existing function signatures are unchanged.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE OR REPLACE FUNCTION public.cloud_workbench_transition(p_workbench uuid, p_generation uuid,
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
        (w.state='resuming' AND p_state IN ('resuming','running','retained')) OR
        (w.state='running' AND p_state IN ('pausing','retained')) OR
        (w.state='pausing' AND p_state IN ('paused','retained')) OR
        (w.state='paused' AND p_state IN ('cleanup_pending','retained')) OR
        (w.state='cleanup_pending' AND p_state IN ('retired','retained'))
    ) THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
    IF p_state IN ('cleanup_pending','retired') AND w.unpublished_changes
        AND w.owner_deleted_at IS NULL THEN
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
    -- Recreated compute must stay on the same disk: a resume never swaps workspaces.
    IF w.state='resuming' AND p_resource IS NOT NULL AND w.resource IS NOT NULL
        AND w.resource->>'volume_id' IS DISTINCT FROM p_resource->>'volume_id' THEN
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

CREATE FUNCTION public.cloud_workbench_disk_lost(p_workbench uuid,p_generation uuid,
    p_version bigint,p_operation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
   OR w.operation_id IS DISTINCT FROM p_operation OR p_operation IS NULL
   OR w.operation_until IS NULL OR w.operation_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000';
 END IF;
 IF w.state<>'resuming' THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
 -- The provider confirmed compute and disk are both absent: no copy remains to protect.
 UPDATE public.cloud_workbenches SET state='retired',resource=NULL,unpublished_changes=false,
   version=version+1,operation_until=clock_timestamp(),open_requested=false,
   operation_phase='failed',operation_error='workbench_disk_lost',operation_retryable=true,
   updated_at=clock_timestamp()
   WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE FUNCTION public.cloud_workbench_reclaim_claim(p_workbench uuid,p_generation uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
   OR w.owner_deleted_at IS NULL OR w.state NOT IN ('retained','cleanup_pending')
   OR (w.operation_until IS NOT NULL AND w.operation_until>clock_timestamp())
   OR public.cloud_workbench_has_occupants(w.id,w.generation) THEN
   RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
 END IF;
 UPDATE public.cloud_workbenches SET state='cleanup_pending',version=version+1,
   operation_id=gen_random_uuid(),operation_until=clock_timestamp()+interval '180 seconds',
   updated_at=clock_timestamp()
   WHERE id=w.id RETURNING * INTO w;
 RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='cloud_workbench_connections' THEN
   IF OLD.state<>'released' THEN RAISE EXCEPTION 'workspace_connection_cleanup_required'; END IF;
   RETURN OLD;
 END IF;
 -- Marking the owner deleted is not a lifecycle step: compute keeps running until
 -- reclamation, which waits for every connection to be released.
 IF TG_OP='UPDATE' AND OLD.owner_deleted_at IS NULL AND NEW.owner_deleted_at IS NOT NULL
   AND NEW.state='retained' AND NEW.generation IS NOT DISTINCT FROM OLD.generation THEN
   RETURN NEW;
 END IF;
 IF (TG_OP='DELETE' OR NEW.generation IS DISTINCT FROM OLD.generation OR NEW.state IS DISTINCT FROM OLD.state)
   AND EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE workbench_id=OLD.id AND state<>'released') THEN
   RAISE EXCEPTION 'workspace_connection_cleanup_required';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_disk_lost(uuid,uuid,bigint,uuid),
 public.cloud_workbench_reclaim_claim(uuid,uuid,bigint) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_disk_lost(uuid,uuid,bigint,uuid),
 public.cloud_workbench_reclaim_claim(uuid,uuid,bigint) TO service_role;

COMMIT;
