-- Workspace owners choose what their Cloud Computer provides. "desktop" picks
-- the sandbox Profile of the next computer (an existing binding never changes);
-- runtime toggles such as "screen_stream" withdraw an installed Feature without
-- rebuilding it. Additive: existing rows keep '{}' and the deployment default.
BEGIN;

ALTER TABLE public.cloud_workbenches
    ADD COLUMN sandbox_settings jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.cloud_workbenches
    ADD CONSTRAINT cloud_workbenches_sandbox_settings_shape CHECK (
        jsonb_typeof(sandbox_settings) = 'object'
        AND sandbox_settings - ARRAY['desktop','screen_stream'] = '{}'::jsonb
        AND coalesce(jsonb_typeof(sandbox_settings->'desktop'),'boolean') = 'boolean'
        AND coalesce(jsonb_typeof(sandbox_settings->'screen_stream'),'boolean') = 'boolean'
    );

CREATE FUNCTION public.cloud_workbench_set_sandbox_settings(p_workbench uuid,p_user uuid,
 p_project text,p_settings jsonb,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
 IF NOT FOUND OR w.user_id IS DISTINCT FROM p_user OR w.project_id IS DISTINCT FROM p_project
 OR w.owner_deleted_at IS NOT NULL THEN
  RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 -- The table constraint owns the shape; only these keys are ever replaced.
 UPDATE public.cloud_workbenches SET sandbox_settings=coalesce(p_settings,'{}'::jsonb),
 updated_at=clock_timestamp() WHERE id=w.id RETURNING * INTO w;
 RETURN to_jsonb(w);
END $$;

REVOKE ALL ON FUNCTION public.cloud_workbench_set_sandbox_settings(uuid,uuid,text,jsonb,jsonb)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cloud_workbench_set_sandbox_settings(uuid,uuid,text,jsonb,jsonb)
    TO service_role;

COMMIT;
