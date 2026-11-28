-- Additive: enable Tier 2 (fly_host_export) per-Agent compute + Project Host storage binding.
-- 1. cloud_workbenches.host_workbench_id links an isolated Tier 2 Agent compute
--    workbench to the Project's shared default Workspace VM that owns the NVMe disk.
-- 2. cloud_workbenches.collaboration_mode records the collaboration mode the
--    compute workbench was provisioned under.
-- 3. access_grant_agent_workspace(uuid, text) ensures the Project's default Host
--    Workspace exists when routing 'fly_host_export' and binds the Agent's
--    dedicated compute workbench to it (target_ref='refs/heads/workspace').
-- 4. cloud_workbench_idle_claim defers pausing a Project Host Workspace while
--    any active 'fly_host_export' Agent workbench is mounting it.
-- 5. cloud_workbench_connection_acquire_in_session and cloud_workbench_connection_update
--    propagate activity + unpublished_changes to host_workbench_id so automatic
--    backup on the Host VM captures every Tier 2 Agent edit.
-- 6. access_agent_link_command('inventory', ...) reports 'refs/heads/workspace'
--    for all live-shared modes ('shared_machine', 'fly_host_export', 'juicefs_shared')
--    while preserving exact top-level keys ('links', 'connections', 'pending').
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

ALTER TABLE public.cloud_workbenches
    ADD COLUMN host_workbench_id uuid REFERENCES public.cloud_workbenches(id) ON DELETE SET NULL;

ALTER TABLE public.cloud_workbenches
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

CREATE INDEX cloud_workbenches_host_active
    ON public.cloud_workbenches(host_workbench_id)
    WHERE host_workbench_id IS NOT NULL AND owner_deleted_at IS NULL;

CREATE OR REPLACE FUNCTION public.access_grant_agent_workspace(p_grant uuid, p_mode text)
RETURNS public.cloud_workbenches LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    g public.access_grants;
    w public.cloud_workbenches;
    host_w public.cloud_workbenches;
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
    ELSIF effective_mode='fly_host_export' THEN
        host_w := public._cloud_workbench_shared_default(g.approved_by, g.project_id);
        w := public.cloud_workbench_external_agent(g.approved_by, g.project_id, g.registration_id);
        IF w.host_workbench_id IS DISTINCT FROM host_w.id
            OR w.collaboration_mode IS DISTINCT FROM 'fly_host_export'
            OR (w.resource IS NULL AND w.target_ref IS DISTINCT FROM 'refs/heads/workspace') THEN
            UPDATE public.cloud_workbenches
            SET host_workbench_id=host_w.id,
                collaboration_mode='fly_host_export',
                target_ref=CASE WHEN resource IS NULL THEN 'refs/heads/workspace' ELSE target_ref END,
                updated_at=clock_timestamp()
            WHERE id=w.id
            RETURNING * INTO w;
        END IF;
    ELSE
        w := public.cloud_workbench_external_agent(g.approved_by, g.project_id, g.registration_id);
        IF w.collaboration_mode IS DISTINCT FROM effective_mode THEN
            UPDATE public.cloud_workbenches
            SET collaboration_mode=effective_mode,
                updated_at=clock_timestamp()
            WHERE id=w.id
            RETURNING * INTO w;
        END IF;
    END IF;
    IF g.workbench_id IS DISTINCT FROM w.id OR g.collaboration_mode IS DISTINCT FROM effective_mode THEN
        UPDATE public.access_grants
        SET workbench_id=w.id, collaboration_mode=effective_mode
        WHERE id=g.id;
    END IF;
    RETURN w;
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
        OR (p_action='pause' AND w.is_default AND w.agent_registration_id IS NULL AND EXISTS(
            SELECT 1 FROM public.cloud_workbenches child
            WHERE child.host_workbench_id=w.id
              AND child.owner_deleted_at IS NULL
              AND child.state IN ('allocating','resuming','running','pausing')
        ))
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

CREATE OR REPLACE FUNCTION public.cloud_workbench_connection_acquire_in_session(
    p_id uuid,
    p_session uuid,
    p_workbench uuid,
    p_generation uuid,
    p_user uuid,
    p_project text,
    p_revision jsonb
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; c public.cloud_workbench_connections; uid integer;
BEGIN
    PERFORM public.authorization_assert_revision(p_project,p_revision);
    PERFORM pg_advisory_xact_lock(hashtextextended('cloud_workbench_connection_user:'||p_user::text,0));
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation
        OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, true)
        OR NOT (w.is_default OR w.agent_registration_id IS NOT NULL)
        OR w.state<>'running' OR w.resource IS NULL OR coalesce(w.resource->>'allocation_pending','true')<>'false' THEN
        RAISE EXCEPTION 'workspace_connection_denied' USING ERRCODE='42501';
    END IF;
    IF p_id IS NULL OR EXISTS(SELECT 1 FROM public.cloud_workbench_connections WHERE id=p_id) THEN
        RAISE EXCEPTION 'workspace_connection_already_used' USING ERRCODE='42501';
    END IF;
    IF (SELECT count(*) FROM public.cloud_workbench_connections WHERE workbench_id=w.id AND state<>'released')>=10 THEN
        RAISE EXCEPTION 'workspace_connection_limit' USING ERRCODE='53400', DETAIL='workspace';
    END IF;
    IF w.agent_registration_id IS NULL AND (SELECT count(*) FROM public.cloud_workbench_connections x
        JOIN public.cloud_workbenches o ON o.id=x.workbench_id
        WHERE x.user_id=p_user AND x.state<>'released' AND o.agent_registration_id IS NULL)>=20 THEN
        RAISE EXCEPTION 'workspace_connection_limit' USING ERRCODE='53400', DETAIL='owner';
    END IF;
    uid:=public.cloud_workbench_task_uid(w);
    INSERT INTO public.cloud_workbench_connections(id,workbench_id,generation,user_id,project_id,task_uid,resource,binding,compute_epoch,
        access_session_id)
    VALUES(p_id,w.id,w.generation,p_user,p_project,uid,
        w.resource || jsonb_build_object('task_execution_id',p_id,'task_uid',uid),w.binding,w.compute_epoch,
        p_session) RETURNING * INTO c;
    UPDATE public.cloud_workbenches SET next_task_uid=uid+1,last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
    IF w.host_workbench_id IS NOT NULL THEN
        UPDATE public.cloud_workbenches
        SET last_activity_at=clock_timestamp(), unpublished_changes=true, updated_at=clock_timestamp()
        WHERE id=w.host_workbench_id AND owner_deleted_at IS NULL;
    END IF;
    RETURN to_jsonb(c);
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
        IF w.host_workbench_id IS NOT NULL THEN
            UPDATE public.cloud_workbenches
            SET last_activity_at=clock_timestamp(), unpublished_changes=true, updated_at=clock_timestamp()
            WHERE id=w.host_workbench_id AND owner_deleted_at IS NULL;
        END IF;
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
        IF w.host_workbench_id IS NOT NULL THEN
            UPDATE public.cloud_workbenches
            SET last_activity_at=clock_timestamp(), unpublished_changes=true, updated_at=clock_timestamp()
            WHERE id=w.host_workbench_id AND owner_deleted_at IS NULL;
        END IF;
    ELSE RAISE EXCEPTION 'workspace_connection_invalid_action'; END IF;
    RETURN to_jsonb(c);
END $$;

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
                    WHEN parent.collaboration_mode IN ('shared_machine','fly_host_export','juicefs_shared')
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
    public.access_grant_agent_workspace(uuid, text),
    public.cloud_workbench_idle_claim(uuid, uuid, bigint, text),
    public.cloud_workbench_connection_acquire_in_session(uuid, uuid, uuid, uuid, uuid, text, jsonb),
    public.cloud_workbench_connection_update(uuid, uuid, text),
    public.access_agent_link_command(text, uuid, jsonb)
FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.access_grant_agent_workspace(uuid, text) FROM service_role;

GRANT EXECUTE ON FUNCTION
    public.cloud_workbench_idle_claim(uuid, uuid, bigint, text),
    public.cloud_workbench_connection_acquire_in_session(uuid, uuid, uuid, uuid, uuid, text, jsonb),
    public.cloud_workbench_connection_update(uuid, uuid, text),
    public.access_agent_link_command(text, uuid, jsonb)
TO service_role;

COMMIT;
