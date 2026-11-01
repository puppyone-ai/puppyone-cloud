-- Additive: a Workspace can refuse an operation, but it never stops half way.
-- 1. A pause has a time limit. Past it the machine returns to running, or to retained
--    when nobody can tell what it is doing.
-- 2. retained has exits: back to paused once the one original resource is confirmed
--    stopped, and back to retired when nothing was ever created. Only what cannot be
--    checked stays, and maintenance reports it.
-- 3. A disk lost during a pause ends in retired, as it does during a resume.
-- 4. A failed reclaim of a live owner's Workspace is retried by maintenance.
-- 5. Each save records its result, so a failure is visible instead of blocking the pause.
-- Function signatures are unchanged, so callers from the previous release keep working.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.cloud_workbenches
    ADD COLUMN pausing_since timestamptz,
    ADD COLUMN save_status text CHECK (save_status IN ('saved','merging','rejected','failed','skipped')),
    ADD COLUMN save_error text CHECK (save_error ~ '^workbench_[a-z_]{1,80}$'),
    ADD COLUMN saved_oid text CHECK (saved_oid ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    ADD COLUMN save_finished_at timestamptz;

UPDATE public.cloud_workbenches SET pausing_since=updated_at WHERE state='pausing';

CREATE OR REPLACE FUNCTION public.cloud_workbench_occupancy_guard() RETURNS trigger
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
    IF NEW.state='pausing' AND OLD.state<>'pausing' THEN NEW.pausing_since:=clock_timestamp();
    ELSIF NEW.state<>'pausing' THEN NEW.pausing_since:=NULL; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_transition(p_workbench uuid, p_generation uuid,
    p_version bigint, p_operation uuid, p_state text, p_resource jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; resource_value jsonb; never_created boolean;
BEGIN
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
        OR w.operation_id IS DISTINCT FROM p_operation OR p_operation IS NULL
        OR w.operation_until IS NULL OR w.operation_until<=clock_timestamp() THEN
        RAISE EXCEPTION 'workbench_fenced' USING ERRCODE='55000';
    END IF;
    -- No compute and no disk ever existed for this intent, so no copy needs protecting.
    never_created := w.resource IS NULL OR (w.resource->>'allocation_pending'='true'
        AND w.resource->>'volume_id' IS NULL);
    IF NOT (
        (w.state='allocating' AND p_state IN ('allocating','running','retained')) OR
        (w.state='resuming' AND p_state IN ('resuming','running','retained')) OR
        (w.state='running' AND p_state IN ('pausing','retained')) OR
        (w.state='pausing' AND p_state IN ('paused','retained','running')) OR
        (w.state='paused' AND p_state IN ('cleanup_pending','retained')) OR
        (w.state='cleanup_pending' AND p_state IN ('retired','retained')) OR
        (w.state='retained' AND (p_state='paused' OR (p_state='retired' AND never_created)))
    ) THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
    IF p_state IN ('cleanup_pending','retired') AND w.unpublished_changes
        AND w.owner_deleted_at IS NULL AND NOT (w.state='retained' AND never_created) THEN
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

CREATE OR REPLACE FUNCTION public.cloud_workbench_disk_lost(p_workbench uuid,p_generation uuid,
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
 IF w.state NOT IN ('resuming','pausing') THEN RAISE EXCEPTION 'workbench_transition_invalid'; END IF;
 -- The provider confirmed compute and disk are both absent: no copy remains to protect.
 UPDATE public.cloud_workbenches SET state='retired',resource=NULL,unpublished_changes=false,
   version=version+1,operation_until=clock_timestamp(),open_requested=false,
   operation_phase='failed',operation_error='workbench_disk_lost',operation_retryable=true,
   updated_at=clock_timestamp()
   WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_reclaim_claim(p_workbench uuid,p_generation uuid,p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 -- A live owner's Workspace reaches cleanup_pending only with its work saved, so a
 -- failed reclaim is retried as well. A retained one of theirs is never destroyed.
 IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
   OR (w.owner_deleted_at IS NULL AND w.state<>'cleanup_pending')
   OR w.state NOT IN ('retained','cleanup_pending')
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

-- Only a pausing Workspace is final: nothing new can connect. Anything but 'saved'
-- leaves unpublished_changes set, so the machine is kept and never reclaimed.
CREATE FUNCTION public.cloud_workbench_save_outcome(p_workbench uuid,p_generation uuid,
    p_status text,p_error text DEFAULT NULL,p_oid text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 UPDATE public.cloud_workbenches SET save_status=p_status,save_error=p_error,
   saved_oid=CASE WHEN p_status='saved' THEN coalesce(p_oid,saved_oid) ELSE saved_oid END,
   unpublished_changes=CASE WHEN p_status='saved' THEN false ELSE unpublished_changes END,
   save_finished_at=clock_timestamp(),updated_at=clock_timestamp()
 WHERE id=p_workbench AND generation=p_generation AND state='pausing'
   AND agent_registration_id IS NOT NULL RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_save_outcome(uuid,uuid,text,text,text)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_save_outcome(uuid,uuid,text,text,text) TO service_role;

COMMIT;
