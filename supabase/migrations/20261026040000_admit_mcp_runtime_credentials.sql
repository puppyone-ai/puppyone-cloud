-- Admit Project-root MCP keys to native repository reads and publications.
-- Additive: a new resolver plus a compatible replacement of the actor fence.
-- An MCP bearer credential acts with its endpoint owner's CURRENT Project role:
-- removal denies it, a Viewer owner caps it to read-only, and the endpoint's
-- filesystem policy can only narrow that. Git credentials are unchanged.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

CREATE FUNCTION public.resolve_mcp_runtime_credential(p_credential_id text)
RETURNS TABLE(credential_id text,org_id text,project_id text,access_surface_id text,
 owner_id uuid,effective_mode text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT c.id,c.org_id,c.project_id,c.access_surface_id,s.created_by,
   CASE WHEN role.effective_role IN ('admin','editor')
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(
           CASE WHEN jsonb_typeof(policy.fs_policy->'accesses')='array'
                THEN policy.fs_policy->'accesses' ELSE '[]'::jsonb END) access
         WHERE jsonb_typeof(access)='object' AND access->'readonly'='false'::jsonb)
   THEN 'rw' ELSE 'r' END
 FROM public.access_surface_credentials c
 JOIN public.projects p ON p.id=c.project_id AND p.org_id=c.org_id AND p.lifecycle_status='ready'
 JOIN public.access_surfaces s ON s.id=c.access_surface_id AND s.project_id=c.project_id
   AND s.org_id=c.org_id AND s.kind='mcp' AND s.status='active' AND s.scope_id IS NULL
   AND s.created_by IS NOT NULL
 LEFT JOIN public.access_surface_policies policy ON policy.access_surface_id=s.id
 JOIN LATERAL public.resolve_project_role(c.project_id,s.created_by) role ON role.org_id=c.org_id
 WHERE c.id=p_credential_id AND c.credential_type='bearer_token' AND c.status='active'
   AND (c.expires_at IS NULL OR c.expires_at>clock_timestamp())
 LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public._version_assert_current_repository_actor(p_project_id text,p_org_id text,p_actor text,p_write boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE subject_user_id uuid; hint public.access_surface_credentials%ROWTYPE;
    credential public.access_surface_credentials%ROWTYPE; allowed boolean; resolved record;
BEGIN
    -- Call only after Organization -> Project locking. Lock membership before
    -- Surface -> credential, matching membership/surface deletion cascades.
    -- The platform's canonical resolvers still decide roles and narrowed modes.
    IF p_actor LIKE 'initialize:%' THEN
        -- Only the already admitted initializer lease may see/write the hidden
        -- repository. Human and Runtime credentials remain denied until ready.
        IF NOT EXISTS(SELECT 1 FROM public.projects p
            JOIN public.project_create_operations o ON o.project_id=p.id AND o.actor_user_id=p.created_by
            JOIN public.project_write_leases l ON l.project_id=p.id
            JOIN public.org_members om ON om.org_id=p.org_id AND om.user_id=o.actor_user_id
            JOIN public.project_members pm ON pm.project_id=p.id AND pm.user_id=o.actor_user_id AND pm.org_id=p.org_id
            WHERE p.id=p_project_id AND p.org_id=p_org_id AND p.lifecycle_status='initializing'
              AND o.status='initializing' AND l.id::text=substring(p_actor FROM 12)
              AND l.expires_at>clock_timestamp() AND pm.role='admin') THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END IF;
        PERFORM set_config('puppyone.native_initializer',p_actor,true);
        RETURN;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project_id AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
    IF p_actor LIKE 'user:%' THEN
        BEGIN subject_user_id := substring(p_actor FROM 6)::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END;
    ELSIF p_actor LIKE 'runtime:%' THEN
        SELECT * INTO hint FROM public.access_surface_credentials
            WHERE id=substring(p_actor FROM 9) AND project_id=p_project_id AND org_id=p_org_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
        IF hint.credential_type = 'bearer_token' THEN
            -- MCP keys act with their endpoint owner's current Project role.
            SELECT s.created_by INTO subject_user_id FROM public.access_surfaces s
                WHERE s.id=hint.access_surface_id AND s.project_id=p_project_id AND s.org_id=p_org_id;
            IF subject_user_id IS NULL THEN
                RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
            END IF;
        ELSE
            subject_user_id := hint.user_id;
        END IF;
    ELSE
        RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
    END IF;
    IF subject_user_id IS NOT NULL THEN
        PERFORM 1 FROM public.org_members m WHERE m.org_id=p_org_id AND m.user_id=subject_user_id FOR SHARE;
        PERFORM 1 FROM public.project_members m WHERE m.project_id=p_project_id AND m.user_id=subject_user_id FOR SHARE;
        -- Runtime resolution below already checks this user's current role.
        -- Membership rows stay locked for both paths; do not resolve twice.
        IF p_actor LIKE 'user:%' THEN
            SELECT (r.effective_role IN ('admin','editor')) INTO allowed
                FROM public.resolve_project_role(p_project_id,subject_user_id) r;
            IF NOT FOUND OR (p_write AND NOT allowed) THEN
                RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    IF p_actor LIKE 'runtime:%' THEN
        PERFORM 1 FROM public.access_surfaces s WHERE s.id=hint.access_surface_id
            AND s.project_id=p_project_id AND s.org_id=p_org_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501'; END IF;
        SELECT * INTO credential FROM public.access_surface_credentials WHERE id=hint.id FOR SHARE;
        IF NOT FOUND OR credential.project_id IS DISTINCT FROM p_project_id
           OR credential.org_id IS DISTINCT FROM p_org_id
           OR credential.user_id IS DISTINCT FROM hint.user_id
           OR credential.access_surface_id IS DISTINCT FROM hint.access_surface_id
           OR credential.status <> 'active'
           OR (credential.expires_at IS NOT NULL AND credential.expires_at <= clock_timestamp()) THEN
            RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
        END IF;
        IF credential.credential_type = 'bearer_token' THEN
            SELECT * INTO resolved FROM public.resolve_mcp_runtime_credential(credential.id) r;
            IF NOT FOUND OR resolved.project_id IS DISTINCT FROM p_project_id
               OR resolved.org_id IS DISTINCT FROM p_org_id
               OR resolved.owner_id IS DISTINCT FROM subject_user_id
               OR (p_write AND resolved.effective_mode <> 'rw') THEN
                RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
            END IF;
        ELSE
            SELECT * INTO resolved FROM public.resolve_git_runtime_credential(credential.key_hash) r
                WHERE r.credential_id=credential.id;
            IF NOT FOUND OR resolved.project_id IS DISTINCT FROM p_project_id
               OR resolved.org_id IS DISTINCT FROM p_org_id OR resolved.target_kind <> 'project_root'
               OR (p_write AND resolved.effective_mode <> 'rw') THEN
                RAISE EXCEPTION 'repository_action_denied' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
END $$;

REVOKE ALL ON FUNCTION public.resolve_mcp_runtime_credential(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_mcp_runtime_credential(text) TO service_role;

COMMIT;
