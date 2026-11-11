-- Default is a navigation preference, not a distinct tenancy model.
-- Keep every organization/project ID and the historical identity column.
BEGIN;

-- Before admitting more people, preserve the effective privacy of old personal
-- projects. Explicit project memberships, content and repository state remain.
UPDATE public.projects p SET visibility = 'private'
FROM public.organizations o
WHERE p.org_id = o.id AND o.personal_owner_id IS NOT NULL AND p.visibility = 'org';

DROP TRIGGER organizations_personal_identity ON public.organizations;
DROP TRIGGER org_members_personal_admission ON public.org_members;
DROP TRIGGER org_invitations_personal_admission ON public.org_invitations;

-- This column is historical provenance only. A growing Space must never be
-- deleted as a side effect of removing its original personal account.
ALTER TABLE public.organizations DROP CONSTRAINT organizations_personal_owner_id_fkey;
ALTER TABLE public.organizations ADD CONSTRAINT organizations_personal_owner_id_fkey
    FOREIGN KEY (personal_owner_id) REFERENCES auth.users(id) ON DELETE SET NULL;

CREATE FUNCTION public.ensure_default_cloud_space(p_user uuid, p_name text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE space public.organizations%ROWTYPE; default_id text; was_created boolean := false;
BEGIN
    IF p_user IS NULL THEN RAISE EXCEPTION 'Space requires an initialized account' USING ERRCODE = '22023'; END IF;
    SELECT default_org_id INTO default_id FROM public.profiles WHERE user_id = p_user FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Space requires an initialized account' USING ERRCODE = '22023'; END IF;
    SELECT o.* INTO space FROM public.organizations o
      JOIN public.org_members m ON m.org_id = o.id AND m.user_id = p_user
      WHERE o.id = default_id;
    IF NOT FOUND THEN
        SELECT o.* INTO space FROM public.organizations o
          JOIN public.org_members m ON m.org_id = o.id AND m.user_id = p_user
          ORDER BY (o.personal_owner_id = p_user) DESC NULLS LAST, m.joined_at, o.id LIMIT 1;
    END IF;
    IF space.id IS NULL THEN
        INSERT INTO public.organizations(id, name, slug, type, created_by)
        VALUES(gen_random_uuid()::text, coalesce(nullif(left(btrim(p_name), 120), ''), 'My space'),
            'space-' || gen_random_uuid()::text, 'team', p_user) RETURNING * INTO space;
        INSERT INTO public.org_members(org_id, user_id, role) VALUES(space.id, p_user, 'owner');
        was_created := true;
    END IF;
    UPDATE public.profiles SET default_org_id = space.id WHERE user_id = p_user;
    RETURN jsonb_build_object('organization', to_jsonb(space), 'created', was_created);
END;
$$;
REVOKE ALL ON FUNCTION public.ensure_default_cloud_space(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ensure_default_cloud_space(uuid, text) TO service_role;

-- Compatibility entry point for old clients. It no longer creates another
-- private silo merely because an account's accessible default is a team.
CREATE OR REPLACE FUNCTION public.ensure_personal_cloud_space(p_user uuid, p_name text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN RETURN public.ensure_default_cloud_space(p_user, p_name); END;
$$;

CREATE OR REPLACE FUNCTION public.delete_empty_organization_control_plane(p_org_id text, p_actor_user_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE actor_role text; actor_org_count bigint;
BEGIN
    IF p_org_id IS NULL OR btrim(p_org_id) = '' OR p_actor_user_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'invalid_request');
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('organization-delete:' || p_actor_user_id::text, 0));
    PERFORM 1 FROM public.organizations WHERE id = p_org_id FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found'); END IF;
    PERFORM 1 FROM public.org_members WHERE user_id = p_actor_user_id ORDER BY org_id FOR UPDATE;
    SELECT role INTO actor_role FROM public.org_members WHERE org_id = p_org_id AND user_id = p_actor_user_id;
    IF actor_role IS DISTINCT FROM 'owner' THEN RETURN jsonb_build_object('outcome', 'forbidden'); END IF;
    SELECT count(*) INTO actor_org_count FROM public.org_members WHERE user_id = p_actor_user_id;
    IF actor_org_count <= 1 THEN RETURN jsonb_build_object('outcome', 'only_organization'); END IF;
    IF EXISTS (SELECT 1 FROM public.projects WHERE org_id = p_org_id) THEN
        RETURN jsonb_build_object('outcome', 'organization_not_empty');
    END IF;
    PERFORM id FROM public.project_deletion_jobs WHERE org_id = p_org_id ORDER BY id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM public.project_deletion_jobs WHERE org_id = p_org_id AND status <> 'completed') THEN
        RETURN jsonb_build_object('outcome', 'organization_deletion_in_progress');
    END IF;
    DELETE FROM public.organizations WHERE id = p_org_id;
    RETURN jsonb_build_object('outcome', 'deleted');
END;
$$;
COMMIT;
