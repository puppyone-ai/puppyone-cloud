-- Additive: pluggable multi-agent workspace collaboration routing.
-- 1. Projects may optionally override the deployment default collaboration mode
--    via projects.agent_collab_mode (NULL follows the backend router default).
-- 2. access_grants.collaboration_mode records the effective mode each workspace
--    grant was seated under ('shared_machine', 'fly_host_export',
--    'juicefs_shared', or 'isolated_branch').
-- 3. Mode-aware overloads for access_grant_agent_workspace, access_agent_workspace,
--    access_registered_workspace, access_agent_ssh_ticket and access_issue_credential
--    route 'shared_machine' external Agents onto the Project's single shared default
--    Workspace while enforcing the 5-Agent seat limit across both shared and
--    per-Agent workspaces.
-- 4. Existing RPC signatures remain unchanged and default to
--    coalesce(project.agent_collab_mode, 'isolated_branch'), preserving full
--    compatibility with earlier releases during rolling upgrades.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.projects
    ADD COLUMN agent_collab_mode text
    CHECK (
        agent_collab_mode IS NULL
        OR agent_collab_mode IN (
            'shared_machine',
            'fly_host_export',
            'juicefs_shared',
            'isolated_branch'
        )
    );

ALTER TABLE public.access_grants
    ADD COLUMN collaboration_mode text
    CHECK (
        collaboration_mode IS NULL
        OR collaboration_mode IN (
            'shared_machine',
            'fly_host_export',
            'juicefs_shared',
            'isolated_branch'
        )
    );

CREATE INDEX access_grants_shared_collab_seats
    ON public.access_grants(project_id, registration_id)
    WHERE target_kind='workspace' AND collaboration_mode='shared_machine' AND state='active';

-- Internal helper: resolve effective collaboration mode (project override wins over caller default).
CREATE FUNCTION public._cloud_workbench_resolve_collab_mode(p_project text, p_mode text)
RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    project_mode text;
    effective text;
BEGIN
    IF p_mode IS NOT NULL AND p_mode NOT IN (
        'shared_machine',
        'fly_host_export',
        'juicefs_shared',
        'isolated_branch'
    ) THEN
        RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT agent_collab_mode INTO project_mode
    FROM public.projects
    WHERE id=p_project;
    effective := coalesce(project_mode, p_mode, 'isolated_branch');
    RETURN effective;
END $$;

-- Internal helper: count distinct seated external Agents in a Project across
-- both per-Agent workbenches and active shared-machine grants.
CREATE FUNCTION public._cloud_workbench_agent_seat_count(p_project text)
RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT count(DISTINCT reg_id)::integer FROM (
        SELECT cw.agent_registration_id AS reg_id
        FROM public.cloud_workbenches cw
        WHERE cw.project_id=p_project
          AND cw.agent_registration_id IS NOT NULL
          AND cw.owner_deleted_at IS NULL
        UNION
        SELECT ag.registration_id AS reg_id
        FROM public.access_grants ag
        JOIN public.access_registrations ar ON ar.id=ag.registration_id
        WHERE ag.project_id=p_project
          AND ag.target_kind='workspace'
          AND ag.collaboration_mode='shared_machine'
          AND ag.state='active'
          AND ar.state='active'
          AND (ag.expires_at IS NULL OR ag.expires_at>clock_timestamp())
    ) seated
$$;

-- Internal helper: resolve or idempotently create the Project's single shared default Workspace.
CREATE FUNCTION public._cloud_workbench_shared_default(p_user uuid, p_project text)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,is_default,internet_access)
    VALUES(p_user,p_project,'Workspace','refs/heads/workspace',true,true)
    ON CONFLICT(project_id)
        WHERE is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL
        DO NOTHING;
    SELECT * INTO w FROM public.cloud_workbenches
    WHERE project_id=p_project
      AND is_default
      AND owner_deleted_at IS NULL
      AND agent_registration_id IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000';
    END IF;
    RETURN w;
END $$;

-- Keep the per-Agent seat cap consistent across mode transitions.
CREATE OR REPLACE FUNCTION public.cloud_workbench_external_agent(p_user uuid, p_project text, p_registration uuid)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; already_seated boolean;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cloud_workbench_agent_seats:'||p_project,0));
    SELECT * INTO w FROM public.cloud_workbenches
    WHERE project_id=p_project
      AND agent_registration_id=p_registration
      AND owner_deleted_at IS NULL;
    IF FOUND THEN RETURN w; END IF;
    already_seated := EXISTS(
        SELECT 1 FROM public.access_grants ag
        JOIN public.access_registrations ar ON ar.id=ag.registration_id
        WHERE ag.project_id=p_project
          AND ag.registration_id=p_registration
          AND ag.target_kind='workspace'
          AND ag.collaboration_mode='shared_machine'
          AND ag.state='active'
          AND ar.state='active'
          AND (ag.expires_at IS NULL OR ag.expires_at>clock_timestamp())
    );
    IF NOT already_seated AND public._cloud_workbench_agent_seat_count(p_project)>=5 THEN
        RAISE EXCEPTION 'workspace_agent_seat_limit' USING ERRCODE='53400';
    END IF;
    INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,agent_registration_id,retention_seconds)
    VALUES(p_user,p_project,'External Agent','refs/heads/agents/'||p_registration::text,p_registration,604800)
    RETURNING * INTO w;
    RETURN w;
END $$;

-- Mode-aware workspace routing seam for external Agent grants.
CREATE FUNCTION public.access_grant_agent_workspace(p_grant uuid, p_mode text)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    g public.access_grants;
    w public.cloud_workbenches;
    effective_mode text;
    already_seated boolean;
BEGIN
    SELECT * INTO g FROM public.access_grants WHERE id=p_grant FOR UPDATE;
    IF NOT FOUND OR g.target_kind<>'workspace' THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    effective_mode := public._cloud_workbench_resolve_collab_mode(g.project_id, p_mode);
    IF effective_mode='shared_machine' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('cloud_workbench_agent_seats:'||g.project_id,0));
        already_seated := (
            coalesce(g.collaboration_mode='shared_machine', false)
            OR EXISTS(
                SELECT 1 FROM public.cloud_workbenches cw
                WHERE cw.project_id=g.project_id
                  AND cw.agent_registration_id=g.registration_id
                  AND cw.owner_deleted_at IS NULL
            )
            OR EXISTS(
                SELECT 1 FROM public.access_grants ag
                JOIN public.access_registrations ar ON ar.id=ag.registration_id
                WHERE ag.project_id=g.project_id
                  AND ag.registration_id=g.registration_id
                  AND ag.target_kind='workspace'
                  AND ag.collaboration_mode='shared_machine'
                  AND ag.state='active'
                  AND ar.state='active'
                  AND (ag.expires_at IS NULL OR ag.expires_at>clock_timestamp())
            )
        );
        IF NOT already_seated AND public._cloud_workbench_agent_seat_count(g.project_id)>=5 THEN
            RAISE EXCEPTION 'workspace_agent_seat_limit' USING ERRCODE='53400';
        END IF;
        w := public._cloud_workbench_shared_default(g.approved_by, g.project_id);
    ELSE
        -- 'isolated_branch', 'fly_host_export', and 'juicefs_shared' allocate a
        -- dedicated per-Agent compute workbench; Tier 2/3 storage mount setup is
        -- orchestrated by the backend topology router.
        w := public.cloud_workbench_external_agent(g.approved_by, g.project_id, g.registration_id);
    END IF;
    IF g.workbench_id IS DISTINCT FROM w.id OR g.collaboration_mode IS DISTINCT FROM effective_mode THEN
        UPDATE public.access_grants
        SET workbench_id=w.id, collaboration_mode=effective_mode
        WHERE id=g.id;
    END IF;
    RETURN w;
END $$;

-- Preserve 1-arg signature for previous releases and direct SQL callers.
CREATE OR REPLACE FUNCTION public.access_grant_agent_workspace(p_grant uuid)
RETURNS public.cloud_workbenches LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.access_grant_agent_workspace(p_grant, NULL::text)
$$;

CREATE FUNCTION public.access_agent_workspace(p_id uuid, p_mode text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    c public.access_agent_connections;
    g public.access_grants;
    w public.cloud_workbenches;
    effective_mode text;
    actor_id uuid;
BEGIN
    SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
    IF NOT FOUND OR c.grant_id IS NULL THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    PERFORM public.access_grant_context(c.grant_id,'ssh');
    SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
    IF NOT('workspace.shell'=ANY(g.capabilities)) THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    effective_mode := public._cloud_workbench_resolve_collab_mode(g.project_id, p_mode);
    w := public.access_grant_agent_workspace(g.id, p_mode);
    actor_id := CASE WHEN w.is_default AND w.agent_registration_id IS NULL THEN g.approved_by ELSE w.user_id END;
    RETURN to_jsonb(w) || jsonb_build_object(
        'actor_user_id', actor_id,
        'collaboration_mode', effective_mode
    );
END $$;

CREATE OR REPLACE FUNCTION public.access_agent_workspace(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_agent_connections; g public.access_grants;
BEGIN
    SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
    IF NOT FOUND OR c.grant_id IS NULL THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    PERFORM public.access_grant_context(c.grant_id,'ssh');
    SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
    IF NOT('workspace.shell'=ANY(g.capabilities)) THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    RETURN to_jsonb(public.access_grant_agent_workspace(g.id, NULL::text));
END $$;

CREATE FUNCTION public.access_registered_workspace(p_key_hashes text[], p_grant uuid, p_mode text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    k public.access_registration_credentials;
    g public.access_grants;
    w public.cloud_workbenches;
    effective_mode text;
    actor_id uuid;
BEGIN
    SELECT * INTO k FROM public.access_registration_credentials
    WHERE key_hash=ANY(p_key_hashes) AND state='active' AND expires_at>clock_timestamp();
    SELECT * INTO g FROM public.access_grants WHERE id=p_grant AND registration_id=k.registration_id;
    IF k.id IS NULL OR g.id IS NULL THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    PERFORM public.access_grant_context(g.id,'ssh');
    effective_mode := public._cloud_workbench_resolve_collab_mode(g.project_id, p_mode);
    w := public.access_grant_agent_workspace(g.id, p_mode);
    actor_id := CASE WHEN w.is_default AND w.agent_registration_id IS NULL THEN g.approved_by ELSE w.user_id END;
    RETURN to_jsonb(w) || jsonb_build_object(
        'actor_user_id', actor_id,
        'collaboration_mode', effective_mode
    );
END $$;

CREATE OR REPLACE FUNCTION public.access_registered_workspace(p_key_hashes text[], p_grant uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE k public.access_registration_credentials; g public.access_grants;
BEGIN
    SELECT * INTO k FROM public.access_registration_credentials
    WHERE key_hash=ANY(p_key_hashes) AND state='active' AND expires_at>clock_timestamp();
    SELECT * INTO g FROM public.access_grants WHERE id=p_grant AND registration_id=k.registration_id;
    IF k.id IS NULL OR g.id IS NULL THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    PERFORM public.access_grant_context(g.id,'ssh');
    RETURN to_jsonb(public.access_grant_agent_workspace(g.id, NULL::text));
END $$;

CREATE FUNCTION public.access_agent_ssh_ticket(p_id uuid, p_mode text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    c public.access_agent_connections;
    g public.access_grants;
    w public.cloud_workbenches;
    ticket uuid := gen_random_uuid();
    ticket_user uuid;
BEGIN
    SELECT * INTO c FROM public.access_agent_connections WHERE id=p_id AND state='approved';
    IF NOT FOUND OR c.grant_id IS NULL THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    PERFORM public.access_grant_context(c.grant_id,'ssh');
    SELECT * INTO g FROM public.access_grants WHERE id=c.grant_id;
    IF g.target_kind<>'workspace' OR NOT('workspace.shell'=ANY(g.capabilities)) THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    w := public.access_grant_agent_workspace(g.id, p_mode);
    IF w.owner_deleted_at IS NOT NULL OR NOT (w.state='running' OR ('workspace.wake'=ANY(g.capabilities)
        AND w.state IN ('paused','pausing','resuming'))) THEN
        RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000';
    END IF;
    ticket_user := CASE WHEN w.is_default AND w.agent_registration_id IS NULL THEN g.approved_by ELSE w.user_id END;
    INSERT INTO public.workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at,access_grant_id)
    VALUES(ticket,w.id,w.generation,ticket_user,w.project_id,c.public_key,
        least(clock_timestamp()+interval '2 minutes',g.expires_at),g.id);
    INSERT INTO public.access_lifecycle_events(registration_id,grant_id,event) VALUES(g.registration_id,g.id,'credential.issued');
    RETURN jsonb_build_object('id',ticket);
END $$;

CREATE OR REPLACE FUNCTION public.access_agent_ssh_ticket(p_id uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.access_agent_ssh_ticket(p_id, NULL::text)
$$;

-- 10-argument mode-aware overload (no defaults on any param to avoid overload ambiguity with 9-arg).
CREATE FUNCTION public.access_issue_credential(
    p_key_hashes text[],
    p_next_key_hash text,
    p_request uuid,
    p_grant uuid,
    p_channel text,
    p_credential_hash text,
    p_public_key text,
    p_surface text,
    p_ttl integer,
    p_mode text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    k public.access_registration_credentials;
    r public.access_registrations;
    g public.access_grants;
    s public.access_surfaces;
    w public.cloud_workbenches;
    context jsonb;
    result jsonb;
    expiry timestamptz;
    ticket_user uuid;
    fingerprint jsonb := jsonb_build_object(
        'credential_hash', p_credential_hash,
        'public_key', p_public_key,
        'surface', p_surface,
        'ttl', p_ttl
    );
BEGIN
    SELECT * INTO k FROM public.access_registration_credentials WHERE key_hash=ANY(p_key_hashes);
    IF NOT FOUND THEN RETURN jsonb_build_object('error','access_invalid_credential'); END IF;
    SELECT * INTO r FROM public.access_registrations WHERE id=k.registration_id FOR UPDATE;
    SELECT * INTO k FROM public.access_registration_credentials WHERE id=k.id FOR UPDATE;
    IF r.state<>'active' OR k.state='revoked' OR k.expires_at<=clock_timestamp() THEN
        RETURN jsonb_build_object('error','access_invalid_credential');
    END IF;
    IF k.state='rotated' THEN
        IF k.request_id=p_request AND k.next_key_hash=p_next_key_hash
            AND k.receipt->>'grant_id'=p_grant::text AND k.receipt->>'channel'=p_channel
            AND k.receipt->'_request'=fingerprint THEN
            PERFORM public.access_grant_context(p_grant,p_channel);
            RETURN k.receipt-'_request';
        END IF;
        UPDATE public.access_registrations SET state='suspended',revision=revision+1,updated_at=clock_timestamp() WHERE id=r.id;
        PERFORM public.access_revoke_registration_credentials(r.id);
        UPDATE public.access_sessions SET state='stopping' WHERE registration_id=r.id AND state='active';
        INSERT INTO public.access_lifecycle_events(registration_id,event) VALUES(r.id,'registration.credential_reused');
        RETURN jsonb_build_object('error','access_credential_reused');
    END IF;
    IF p_next_key_hash IS NULL OR length(p_next_key_hash)<>64 OR p_next_key_hash=k.key_hash
        OR p_request IS NULL OR p_ttl NOT BETWEEN 60 AND 3600 THEN
        RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO g FROM public.access_grants WHERE id=p_grant FOR UPDATE;
    IF NOT FOUND OR g.registration_id<>r.id THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    context := public.access_grant_context(g.id,p_channel);
    expiry := least(clock_timestamp()+make_interval(secs=>p_ttl),g.expires_at);
    IF p_channel='ssh' THEN
        IF g.target_kind<>'workspace' OR NOT('workspace.shell'=ANY(g.capabilities))
            OR p_public_key IS NULL OR p_public_key NOT LIKE 'ssh-ed25519 %' THEN
            RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
        END IF;
        w := public.access_grant_agent_workspace(g.id, p_mode);
        IF w.owner_deleted_at IS NOT NULL OR NOT (w.state='running' OR ('workspace.wake'=ANY(g.capabilities)
            AND w.state IN ('paused','pausing','resuming'))) THEN
            RAISE EXCEPTION 'access_workspace_unavailable' USING ERRCODE='55000';
        END IF;
        ticket_user := CASE WHEN w.is_default AND w.agent_registration_id IS NULL THEN g.approved_by ELSE w.user_id END;
        INSERT INTO public.workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at,access_grant_id)
        VALUES(p_request,w.id,w.generation,ticket_user,w.project_id,p_public_key,expiry,g.id);
        result := jsonb_build_object('id',p_request,'grant_id',g.id,'registration_id',r.id,'channel',p_channel,
            'workbench_id',w.id,'generation',w.generation,'expires_at',expiry);
    ELSE
        IF g.target_kind<>'project_root' OR p_credential_hash IS NULL OR length(p_credential_hash)<>64 THEN
            RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
        END IF;
        SELECT * INTO s FROM public.access_surfaces WHERE id=p_surface AND project_id=g.project_id AND org_id=g.org_id
            AND scope_id IS NULL AND status='active' AND kind=CASE p_channel WHEN 'mcp' THEN 'mcp' ELSE 'git_remote' END;
        IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
        INSERT INTO public.access_surface_credentials(id,org_id,project_id,access_surface_id,credential_type,
            key_prefix,key_last4,key_hash,expires_at,created_by,grant_mode,credential_lifecycle,access_grant_id,access_channel)
        VALUES(p_request::text,g.org_id,g.project_id,s.id,CASE p_channel WHEN 'mcp' THEN 'bearer_token' ELSE 'git_http_token' END,
            CASE p_channel WHEN 'mcp' THEN 'mcp' ELSE 'git' END,'****',p_credential_hash,expiry,g.approved_by,
            CASE WHEN 'repository.write'=ANY(g.capabilities) THEN 'rw' ELSE 'r' END,'session',g.id,p_channel);
        result := jsonb_build_object('id',p_request,'grant_id',g.id,'registration_id',r.id,'channel',p_channel,
            'project_id',g.project_id,'access_surface_id',s.id,'expires_at',expiry);
    END IF;
    UPDATE public.access_registration_credentials
    SET state='rotated',request_id=p_request,next_key_hash=p_next_key_hash,
        receipt=result||jsonb_build_object('_request',fingerprint)
    WHERE id=k.id;
    INSERT INTO public.access_registration_credentials(id,registration_id,key_hash,expires_at)
    VALUES(gen_random_uuid(),r.id,p_next_key_hash,clock_timestamp()+interval '90 days');
    INSERT INTO public.access_lifecycle_events(registration_id,grant_id,event) VALUES(r.id,g.id,'credential.issued');
    RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.access_issue_credential(
    p_key_hashes text[],
    p_next_key_hash text,
    p_request uuid,
    p_grant uuid,
    p_channel text,
    p_credential_hash text DEFAULT NULL,
    p_public_key text DEFAULT NULL,
    p_surface text DEFAULT NULL,
    p_ttl integer DEFAULT 900
)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.access_issue_credential(
        p_key_hashes,
        p_next_key_hash,
        p_request,
        p_grant,
        p_channel,
        p_credential_hash,
        p_public_key,
        p_surface,
        p_ttl,
        NULL::text
    )
$$;

-- Report the actual workspace branch and collaboration_mode for shared-machine connections
-- while preserving exact top-level keys ('links', 'connections', 'pending').
CREATE OR REPLACE FUNCTION public.access_agent_link_command(p_action text, p_user uuid, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    i public.access_invitations;
    c public.access_agent_connections;
    g public.access_grants;
    project text := p_payload->>'project_id';
    identity uuid := (p_payload->>'id')::uuid;
    workbench uuid := (p_payload->>'workbench_id')::uuid;
    mode text := p_payload->>'admission';
    key_hash text := p_payload->>'key_hash';
    decision text := p_payload->>'decision';
    organization text;
BEGIN
    IF p_user IS NULL OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    IF p_action='create' THEN
        IF workbench IS NULL OR mode IS NULL OR mode NOT IN ('sign_in','link') OR key_hash IS NULL OR length(key_hash)<>64 THEN
            RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
        END IF;
        organization := public.access_assert_owner(p_user,project,workbench);
        INSERT INTO public.access_invitations(id,key_hash,project_id,org_id,created_by,target_kind,workbench_id,
            capabilities,channels,max_grant_seconds,expires_at,max_approvals,admission)
        VALUES(identity,key_hash,project,organization,p_user,'workspace',workbench,
            ARRAY['workspace.read','workspace.write','workspace.shell','workspace.wake'],ARRAY['ssh'],
            CASE mode WHEN 'link' THEN 604800 ELSE 2592000 END,clock_timestamp()+interval '7 days',1,mode)
        ON CONFLICT(id) DO NOTHING;
        SELECT * INTO i FROM public.access_invitations WHERE id=identity;
        IF i.created_by IS DISTINCT FROM p_user OR i.project_id IS DISTINCT FROM project OR i.key_hash IS DISTINCT FROM key_hash
            OR i.workbench_id IS DISTINCT FROM workbench OR i.admission IS DISTINCT FROM mode THEN
            RAISE EXCEPTION 'access_conflict' USING ERRCODE='42501';
        END IF;
        RETURN to_jsonb(i)-'key_hash';
    ELSIF p_action='inventory' THEN
        PERFORM public.access_assert_owner(p_user,project);
        RETURN jsonb_build_object(
            'links', coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'admission',x.admission,'expires_at',x.expires_at,
                'created_at',x.created_at) ORDER BY x.created_at DESC) FROM public.access_invitations x
                WHERE x.project_id=project AND x.created_by=p_user AND x.admission<>'approval'
                    AND x.state='active' AND x.expires_at>clock_timestamp()), '[]'),
            'connections', coalesce((SELECT jsonb_agg(jsonb_build_object(
                'id', x.id,
                'name', x.name,
                'avatar', x.avatar,
                'admission', l.admission,
                'grant_id', x.grant_id,
                'connected_at', x.decided_at,
                'expires_at', parent.expires_at,
                'collaboration_mode', parent.collaboration_mode,
                'branch', CASE
                    WHEN parent.collaboration_mode='shared_machine'
                        THEN coalesce(wb.target_ref, 'refs/heads/workspace')
                    ELSE 'refs/heads/agents/'||parent.registration_id::text
                END) ORDER BY x.decided_at DESC)
                FROM public.access_agent_connections x
                JOIN public.access_invitations l ON l.id=x.invitation_id
                JOIN public.access_grants parent ON parent.id=x.grant_id
                LEFT JOIN public.cloud_workbenches wb ON wb.id=parent.workbench_id
                WHERE l.project_id=project AND l.created_by=p_user
                    AND public.access_agent_connection_state(x)='approved'), '[]'),
            'pending', coalesce((SELECT jsonb_agg(jsonb_build_object('id',x.id,'name',x.name,'avatar',x.avatar,'admission',l.admission,
                'requested_at',x.created_at,'expires_at',x.expires_at) ORDER BY x.created_at DESC)
                FROM public.access_agent_connections x
                JOIN public.access_invitations l ON l.id=x.invitation_id
                WHERE l.project_id=project AND l.created_by=p_user AND l.admission<>'approval'
                    AND x.state='pending' AND x.expires_at>clock_timestamp()
                    AND l.state='active' AND l.expires_at>clock_timestamp() AND l.approvals<l.max_approvals), '[]'));
    ELSIF p_action='stop' THEN
        PERFORM public.access_assert_owner(p_user,project);
        UPDATE public.access_invitations SET state='revoked'
        WHERE project_id=project AND created_by=p_user AND admission<>'approval' AND state='active';
        FOR g IN SELECT parent.* FROM public.access_grants parent
            JOIN public.access_agent_connections x ON x.grant_id=parent.id
            JOIN public.access_invitations l ON l.id=x.invitation_id
            WHERE l.project_id=project AND l.created_by=p_user AND parent.state<>'revoked' ORDER BY parent.id LOOP
            PERFORM public.access_grant_command('grant_state',p_user,
                jsonb_build_object('id',g.id,'project_id',project,'state','revoked'));
        END LOOP;
        RETURN jsonb_build_object('stopped',true);
    ELSIF p_action NOT IN ('review','decide') THEN
        RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
    END IF;
    SELECT * INTO c FROM public.access_agent_connections WHERE id=identity;
    IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
    IF p_action='decide' THEN
        SELECT * INTO i FROM public.access_invitations WHERE id=c.invitation_id FOR UPDATE;
        SELECT * INTO c FROM public.access_agent_connections WHERE id=identity FOR UPDATE;
    ELSE
        SELECT * INTO i FROM public.access_invitations WHERE id=c.invitation_id;
    END IF;
    PERFORM public.access_assert_owner(p_user,i.project_id,i.workbench_id);
    IF p_action='decide' THEN
        IF decision IS NULL OR decision NOT IN ('approve','reject') THEN
            RAISE EXCEPTION 'access_invalid_request' USING ERRCODE='22023';
        END IF;
        IF (decision='approve' AND c.state='approved') OR (decision='reject' AND c.state='rejected') THEN NULL;
        ELSIF c.state<>'pending' OR c.expires_at<=clock_timestamp() OR i.state<>'active'
            OR i.expires_at<=clock_timestamp() OR i.approvals>=i.max_approvals THEN
            RAISE EXCEPTION 'access_conflict' USING ERRCODE='55000';
        ELSIF decision='approve' THEN c := public.access_agent_admit(c,i,p_user);
        ELSE
            UPDATE public.access_agent_connections SET state='rejected',decided_by=p_user,decided_at=clock_timestamp()
            WHERE id=c.id RETURNING * INTO c;
        END IF;
    END IF;
    RETURN jsonb_build_object('id',c.id,'name',c.name,'avatar',c.avatar,'state',public.access_agent_connection_state(c),
        'project_id',i.project_id,'project_name',(SELECT name FROM public.projects WHERE id=i.project_id),
        'created_at',c.created_at,'expires_at',c.expires_at,'grant_seconds',i.max_grant_seconds);
END $$;

REVOKE ALL ON FUNCTION
    public._cloud_workbench_resolve_collab_mode(text,text),
    public._cloud_workbench_agent_seat_count(text),
    public._cloud_workbench_shared_default(uuid,text),
    public.access_grant_agent_workspace(uuid,text)
    FROM PUBLIC,anon,authenticated,service_role;

REVOKE ALL ON FUNCTION
    public.access_agent_workspace(uuid,text),
    public.access_registered_workspace(text[],uuid,text),
    public.access_agent_ssh_ticket(uuid,text),
    public.access_issue_credential(text[],text,uuid,uuid,text,text,text,text,integer,text)
    FROM PUBLIC,anon,authenticated;

GRANT EXECUTE ON FUNCTION
    public.access_agent_workspace(uuid,text),
    public.access_registered_workspace(text[],uuid,text),
    public.access_agent_ssh_ticket(uuid,text),
    public.access_issue_credential(text[],text,uuid,uuid,text,text,text,text,integer,text)
    TO service_role;

COMMIT;
