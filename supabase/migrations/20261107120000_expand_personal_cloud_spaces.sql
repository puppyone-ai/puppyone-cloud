-- Explicit personal Cloud ownership. Existing organizations are not reclassified:
-- legacy type, slug, creator and member count are not evidence of private ownership.
BEGIN;

ALTER TABLE public.organizations ADD COLUMN personal_owner_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.organizations ADD CONSTRAINT organizations_personal_owner_shape
    CHECK (personal_owner_id IS NULL OR (personal_owner_id = created_by AND type = 'personal'));
CREATE UNIQUE INDEX organizations_personal_owner_unique ON public.organizations(personal_owner_id)
    WHERE personal_owner_id IS NOT NULL;

CREATE FUNCTION public.guard_personal_cloud_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.personal_owner_id IS NOT NULL AND EXISTS (SELECT 1 FROM auth.users WHERE id = OLD.personal_owner_id) THEN
            RAISE EXCEPTION 'Personal Cloud cannot be deleted through team management' USING ERRCODE = '42501';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.personal_owner_id IS DISTINCT FROM OLD.personal_owner_id THEN
        RAISE EXCEPTION 'Personal Cloud ownership is immutable' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER organizations_personal_identity BEFORE UPDATE OR DELETE ON public.organizations
    FOR EACH ROW EXECUTE FUNCTION public.guard_personal_cloud_identity();

CREATE FUNCTION public.guard_personal_cloud_membership() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE owner_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT personal_owner_id INTO owner_id FROM public.organizations WHERE id = OLD.org_id FOR UPDATE;
        IF owner_id IS NOT NULL AND (TG_OP = 'DELETE' OR NEW.org_id IS DISTINCT FROM OLD.org_id
            OR NEW.user_id IS DISTINCT FROM owner_id OR NEW.role <> 'owner') THEN
            RAISE EXCEPTION 'Personal Cloud membership cannot be removed or transferred' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        SELECT personal_owner_id INTO owner_id FROM public.organizations WHERE id = NEW.org_id FOR UPDATE;
        IF owner_id IS NOT NULL AND (NEW.user_id <> owner_id OR NEW.role <> 'owner') THEN
            RAISE EXCEPTION 'Only the owner may belong to a personal Cloud' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER org_members_personal_admission BEFORE INSERT OR UPDATE OR DELETE ON public.org_members
    FOR EACH ROW EXECUTE FUNCTION public.guard_personal_cloud_membership();

CREATE FUNCTION public.guard_personal_cloud_invitation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.organizations WHERE id = NEW.org_id AND personal_owner_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Personal Cloud does not accept team invitations' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER org_invitations_personal_admission BEFORE INSERT OR UPDATE ON public.org_invitations
    FOR EACH ROW EXECUTE FUNCTION public.guard_personal_cloud_invitation();

CREATE FUNCTION public.ensure_personal_cloud_space(p_user uuid, p_name text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE space public.organizations%ROWTYPE; was_created boolean := false;
BEGIN
    IF p_user IS NULL OR NOT EXISTS (SELECT 1 FROM public.profiles WHERE user_id = p_user) THEN
        RAISE EXCEPTION 'Personal Cloud requires an initialized account' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('personal-cloud:' || p_user::text, 0));
    SELECT * INTO space FROM public.organizations WHERE personal_owner_id = p_user;
    IF NOT FOUND THEN
        INSERT INTO public.organizations(id, name, slug, type, created_by, personal_owner_id)
        VALUES(gen_random_uuid(), coalesce(nullif(left(btrim(p_name), 120), ''), 'My Cloud'),
            'personal-cloud-' || gen_random_uuid()::text, 'personal', p_user, p_user)
        RETURNING * INTO space;
        INSERT INTO public.org_members(org_id, user_id, role) VALUES(space.id, p_user, 'owner');
        was_created := true;
    END IF;
    RETURN jsonb_build_object('organization', to_jsonb(space), 'created', was_created);
END;
$$;

REVOKE ALL ON FUNCTION public.ensure_personal_cloud_space(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ensure_personal_cloud_space(uuid, text) TO service_role;
REVOKE ALL ON FUNCTION public.guard_personal_cloud_identity() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.guard_personal_cloud_membership() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.guard_personal_cloud_invitation() FROM PUBLIC, anon, authenticated;

-- Keep team deletion's atomic authorization, quota and cleanup proof. Personal
-- destinations cannot disappear through this team-management entry point.
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
    IF EXISTS (SELECT 1 FROM public.organizations WHERE id = p_org_id AND personal_owner_id IS NOT NULL) THEN
        RETURN jsonb_build_object('outcome', 'personal_organization');
    END IF;
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
REVOKE ALL ON FUNCTION public.delete_empty_organization_control_plane(text, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.delete_empty_organization_control_plane(text, uuid) TO service_role;

COMMIT;
