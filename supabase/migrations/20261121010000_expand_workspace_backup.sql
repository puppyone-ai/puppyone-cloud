-- Additive: periodic and pause-time automatic backup for all Workspaces.
-- 1. backup_until leases a running Workspace during periodic backup so neither
--    a second worker nor idle pause overlaps with it.
-- 2. cloud_workbench_backup_claim atomically claims a running Workspace whose
--    backup interval has elapsed since save_finished_at with newer activity.
-- 3. cloud_workbench_save_outcome accepts both running and pausing states for
--    any Workspace (default, automation, or external Agent) and clears backup_until.
-- 4. cloud_workbench_idle_claim defers idle pause while backup_until is active.
-- 5. cloud_workbench_connection_update advances last_activity_at on renew so
--    long-lived SSH/HTTPS sessions trigger periodic backups.
-- 6. cloud_workbench_record_sync records the Project branch commit a Workspace
--    took in, so its backups can land on main again after main moved without it.
-- Existing function signatures are unchanged.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.cloud_workbenches
    ADD COLUMN backup_until timestamptz,
    -- The Project branch commit this Workspace last took in (a sync from main).
    -- A backup lands on main only while main is a commit the Workspace knows:
    -- its boot base, its last backup or this.
    ADD COLUMN synced_oid text CHECK (synced_oid ~ '^[0-9a-f]{40}([0-9a-f]{24})?$');

CREATE FUNCTION public.cloud_workbench_backup_claim(
    p_workbench uuid,
    p_generation uuid,
    p_interval_seconds integer DEFAULT 300,
    p_lease_seconds integer DEFAULT 180
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
    lease_secs integer := least(greatest(coalesce(p_lease_seconds, 180), 30), 600);
BEGIN
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('acquired',false,'workbench',NULL);
    END IF;
    IF p_interval_seconds IS NULL OR p_interval_seconds<=0
        OR w.generation IS DISTINCT FROM p_generation
        OR w.state<>'running'
        OR w.owner_deleted_at IS NOT NULL
        OR w.open_requested
        OR (w.backup_until IS NOT NULL AND w.backup_until>clock_timestamp()) THEN
        RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
    END IF;
    IF w.save_finished_at IS NULL THEN
        IF NOT w.unpublished_changes THEN
            RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
        END IF;
    ELSE
        IF (w.last_activity_at<=w.save_finished_at AND NOT w.unpublished_changes)
            OR w.save_finished_at+make_interval(secs=>p_interval_seconds)>clock_timestamp() THEN
            RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
        END IF;
    END IF;
    UPDATE public.cloud_workbenches
    SET backup_until=clock_timestamp()+make_interval(secs=>lease_secs),
        updated_at=clock_timestamp()
    WHERE id=w.id RETURNING * INTO w;
    RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_save_outcome(
    p_workbench uuid,
    p_generation uuid,
    p_status text,
    p_error text DEFAULT NULL,
    p_oid text DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    UPDATE public.cloud_workbenches SET
        save_status=p_status,
        save_error=p_error,
        saved_oid=CASE WHEN p_status='saved' THEN coalesce(p_oid,saved_oid) ELSE saved_oid END,
        unpublished_changes=CASE WHEN p_status='saved' THEN false ELSE unpublished_changes END,
        save_finished_at=clock_timestamp(),
        backup_until=NULL,
        updated_at=clock_timestamp()
    WHERE id=p_workbench AND generation=p_generation AND state IN ('running','pausing')
    RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_idle_claim(
    p_workbench uuid,
    p_generation uuid,
    p_version bigint,
    p_action text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    IF p_action IS NULL OR p_action NOT IN ('pause','retire') THEN
        RAISE EXCEPTION 'workbench_transition_invalid';
    END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('acquired',false,'workbench',NULL); END IF;
    IF w.generation IS DISTINCT FROM p_generation OR w.version IS DISTINCT FROM p_version
        OR w.owner_deleted_at IS NOT NULL OR public.cloud_workbench_has_occupants(w.id,w.generation)
        OR (p_action='pause' AND (w.state<>'running'
            OR (w.backup_until IS NOT NULL AND w.backup_until>clock_timestamp())
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

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_update(
    p_id uuid,
    p_generation uuid,
    p_action text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.cloud_workbench_connections; w public.cloud_workbenches;
BEGIN
    -- Lock order matches acquisition and lifecycle (workbench before connection).
    SELECT * INTO c FROM public.cloud_workbench_connections WHERE id=p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'workspace_connection_missing'; END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=c.workbench_id FOR UPDATE;
    SELECT * INTO c FROM public.cloud_workbench_connections WHERE id=p_id FOR UPDATE;
    IF c.generation IS DISTINCT FROM p_generation OR w.generation IS DISTINCT FROM p_generation THEN
        RAISE EXCEPTION 'workspace_connection_fenced';
    END IF;
    IF p_action='renew' THEN
        IF c.state<>'active' OR c.lease_until<=clock_timestamp() OR w.state<>'running'
            OR w.owner_deleted_at IS NOT NULL OR w.binding IS DISTINCT FROM c.binding OR w.compute_epoch<>c.compute_epoch
            OR (c.resource-'task_execution_id'-'task_uid') IS DISTINCT FROM w.resource THEN
            RAISE EXCEPTION 'workspace_connection_expired';
        END IF;
        UPDATE public.cloud_workbench_connections SET lease_until=clock_timestamp()+interval '45 seconds'
            WHERE id=p_id RETURNING * INTO c;
        UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() WHERE id=w.id;
    ELSIF p_action='expire' THEN
        IF c.state='active' AND c.lease_until<=clock_timestamp() THEN
            UPDATE public.cloud_workbench_connections SET state='stopping' WHERE id=p_id RETURNING * INTO c;
        END IF;
    ELSIF p_action='stop' THEN
        UPDATE public.cloud_workbench_connections SET state=CASE WHEN state='released' THEN state ELSE 'stopping' END
            WHERE id=p_id RETURNING * INTO c;
    ELSIF p_action='release' THEN
        IF c.state NOT IN ('stopping','released') THEN RAISE EXCEPTION 'workspace_connection_cleanup_required'; END IF;
        UPDATE public.cloud_workbench_connections SET state='released',released_at=coalesce(released_at,clock_timestamp())
            WHERE id=p_id RETURNING * INTO c;
        UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() WHERE id=w.id;
    ELSE RAISE EXCEPTION 'workspace_connection_invalid_action'; END IF;
    RETURN to_jsonb(c);
END $$;

CREATE FUNCTION public.cloud_workbench_record_sync(
    p_workbench uuid,
    p_generation uuid,
    p_oid text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    IF p_oid IS NULL OR p_oid !~ '^[0-9a-f]{40}([0-9a-f]{24})?$' THEN
        RAISE EXCEPTION 'workbench_sync_oid_invalid' USING ERRCODE='22023';
    END IF;
    -- What was taken in is not on main yet: the next backup carries it.
    UPDATE public.cloud_workbenches SET synced_oid=p_oid,unpublished_changes=true,
        updated_at=clock_timestamp()
    WHERE id=p_workbench AND generation=p_generation AND state IN ('running','pausing')
    RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_record_sync(uuid,uuid,text)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_record_sync(uuid,uuid,text)
    TO service_role;

REVOKE ALL ON FUNCTION public.cloud_workbench_backup_claim(uuid,uuid,integer,integer)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_backup_claim(uuid,uuid,integer,integer)
    TO service_role;

COMMIT;
