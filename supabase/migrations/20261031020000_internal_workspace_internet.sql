-- An internal Workspace runs its Agent on the machine, so it alone reaches the
-- public internet. The database keeps every other Workspace closed.
BEGIN;

UPDATE public.cloud_workbenches SET internet_access=true
WHERE is_default AND NOT internet_access;

ALTER TABLE public.cloud_workbenches
  ADD CONSTRAINT cloud_workbenches_internet_internal_only CHECK (NOT internet_access OR is_default);

CREATE OR REPLACE FUNCTION public.cloud_workbench_default(p_user uuid,p_project text,p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
 PERFORM public.authorization_assert_revision(p_project,p_revision);
 IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready')
   OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
   RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
 END IF;
 INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,is_default,internet_access)
 VALUES(p_user,p_project,'Workspace','refs/heads/workspaces/'||p_user::text,true,true)
 ON CONFLICT(user_id,project_id) WHERE is_default AND owner_deleted_at IS NULL DO NOTHING;
 SELECT * INTO w FROM public.cloud_workbenches WHERE user_id=p_user AND project_id=p_project
   AND is_default AND owner_deleted_at IS NULL;
 RETURN to_jsonb(w);
END $$;

COMMIT;
