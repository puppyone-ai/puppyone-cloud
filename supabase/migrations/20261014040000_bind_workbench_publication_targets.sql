-- Expand: capture new shared Runs' publication target inside the admission transaction.
-- Does not activate compute, rewrite admitted Runs, move repository HEAD or adopt old Chats.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE FUNCTION public.cloud_workbench_target_ref_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE selected text;
BEGIN
    IF NEW.workbench_id IS NULL THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.policy->>'workbench_target_ref' IS DISTINCT FROM NEW.policy->>'workbench_target_ref' THEN
            RAISE EXCEPTION 'workbench_target_ref_immutable';
        END IF;
        RETURN NEW;
    END IF;
    -- cloud_workbench_attach_run runs first (alphabetical trigger ordering) and
    -- owns the row lock plus actor/project/lifecycle admission. Keep that lock
    -- through this capture, so no intervening ref change can retarget this Run.
    SELECT target_ref INTO selected FROM public.cloud_workbenches WHERE id=NEW.workbench_id FOR UPDATE;
    IF selected IS NULL OR (NEW.policy ? 'workbench_target_ref'
        AND NEW.policy->>'workbench_target_ref' IS DISTINCT FROM selected) THEN
        RAISE EXCEPTION 'workbench_target_ref_mismatch';
    END IF;
    NEW.policy := jsonb_set(NEW.policy,'{workbench_target_ref}',to_jsonb(selected));
    RETURN NEW;
END $$;
CREATE TRIGGER cloud_workbench_target_ref_guard BEFORE INSERT OR UPDATE OF policy ON public.agent_runs
    FOR EACH ROW EXECUTE FUNCTION public.cloud_workbench_target_ref_guard();
REVOKE ALL ON FUNCTION public.cloud_workbench_target_ref_guard() FROM PUBLIC,anon,authenticated,service_role;
COMMIT;
