-- Additive: one default Workspace VM per Project, shared by Project members.
-- 1. Existing projects with multiple active default Workspaces keep the most
--    recently active row as default; the rest become non-default with
--    internet_access=false (satisfying cloud_workbenches_internet_internal_only)
--    without deleting data.
-- 2. Default uniqueness moves from (user_id, project_id) to (project_id) for
--    internal default Workspaces (is_default AND owner_deleted_at IS NULL AND
--    agent_registration_id IS NULL), with target_ref='refs/heads/workspace'.
-- 3. access_assert_owner allows any Project admin to delegate the Project's
--    shared default Workspace while keeping external Agent Workspaces scoped
--    to their owner.
-- 4. Workspace context, default, activity, open, settings, SSH connection, run
--    attachment and task acquisition admit authorized Project members onto the
--    shared default Workspace while keeping non-default and external Agent
--    Workspaces owner-scoped.
-- 5. Deleting a user no longer marks the Project's shared default Workspace as
--    owner_deleted_at; Project deletion still marks every Workspace deleted.
-- Existing function signatures are unchanged.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

-- Demote older duplicate default Workspaces per Project before tightening uniqueness.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY project_id
               ORDER BY last_activity_at DESC NULLS LAST,
                        updated_at DESC NULLS LAST,
                        created_at ASC,
                        id ASC
           ) AS rn
    FROM public.cloud_workbenches
    WHERE is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL
)
UPDATE public.cloud_workbenches w
SET is_default=false,
    internet_access=false,
    updated_at=clock_timestamp()
FROM ranked
WHERE w.id=ranked.id AND ranked.rn>1;

CREATE UNIQUE INDEX cloud_workbenches_project_default
    ON public.cloud_workbenches(project_id)
    WHERE is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL;

DROP INDEX IF EXISTS public.cloud_workbenches_default;

-- Internal helper: shared default Workspaces admit authorized Project members
-- (admin/editor for writes/execution, any role for read activity); non-default
-- and external Agent Workspaces remain strictly bound to w.user_id.
CREATE FUNCTION public._cloud_workbench_user_admitted(
    w public.cloud_workbenches,
    p_user uuid,
    p_project text,
    p_require_write boolean DEFAULT true
)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF w.id IS NULL OR p_user IS NULL OR w.project_id IS DISTINCT FROM p_project
        OR w.owner_deleted_at IS NOT NULL THEN
        RETURN false;
    END IF;
    IF w.is_default AND w.agent_registration_id IS NULL THEN
        -- Every member, its first opener too, holds the Project role it needs.
        RETURN EXISTS(
            SELECT 1 FROM public.resolve_project_role(p_project, p_user) r
            WHERE NOT p_require_write OR r.effective_role IN ('admin', 'editor')
        );
    END IF;
    RETURN w.user_id = p_user;
END $$;

REVOKE ALL ON FUNCTION public._cloud_workbench_user_admitted(public.cloud_workbenches,uuid,text,boolean)
    FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.cloud_workbench_context(p_user uuid, p_project text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT jsonb_build_object(
        'facts', public.authorization_project_facts(p_project, p_user),
        'revision', public.authorization_revision_token(p_project),
        'workbench', (
            SELECT to_jsonb(w) FROM public.cloud_workbenches w
            WHERE w.project_id=p_project
              AND w.is_default
              AND w.owner_deleted_at IS NULL
              AND w.agent_registration_id IS NULL
        )
    )
$$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_default(p_user uuid, p_project text, p_revision jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    PERFORM public.authorization_assert_revision(p_project, p_revision);
    IF NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready')
        OR NOT EXISTS(SELECT 1 FROM auth.users WHERE id=p_user) THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,is_default,internet_access)
    VALUES(p_user,p_project,'Workspace','refs/heads/workspace',true,true)
    ON CONFLICT(project_id) WHERE is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL DO NOTHING;
    SELECT * INTO w FROM public.cloud_workbenches
    WHERE project_id=p_project AND is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL;
    RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.access_assert_owner(p_user uuid, p_project text, p_workbench uuid DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority record;
BEGIN
    SELECT * INTO authority FROM public.resolve_project_role(p_project, p_user);
    IF NOT FOUND OR authority.effective_role<>'admin' OR NOT EXISTS(
        SELECT 1 FROM public.projects WHERE id=p_project AND org_id=authority.org_id AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    IF p_workbench IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM public.cloud_workbenches
        WHERE id=p_workbench AND project_id=p_project
          AND ((is_default AND agent_registration_id IS NULL)
               OR (agent_registration_id IS NOT NULL AND user_id=p_user))
          AND owner_deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
    END IF;
    RETURN authority.org_id;
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_activity(
    p_workbench uuid,
    p_generation uuid,
    p_user uuid,
    p_project text,
    p_revision jsonb,
    p_write boolean DEFAULT false
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    PERFORM public.authorization_assert_revision(p_project, p_revision);
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, coalesce(p_write, false))
        OR w.generation IS DISTINCT FROM p_generation THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF w.state<>'running' THEN RAISE EXCEPTION 'workbench_not_running' USING ERRCODE='55000'; END IF;
    UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp(),
        unpublished_changes=unpublished_changes OR p_write WHERE id=w.id RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_acquire(
    p_workbench uuid,
    p_user uuid,
    p_project text,
    p_binding jsonb
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, true)
        OR NOT EXISTS (SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(p_binding) IS DISTINCT FROM 'object'
        OR coalesce(p_binding->>'provider','') !~ '^[a-z][a-z0-9_-]{0,62}$'
        OR length(coalesce(p_binding->>'artifact',''))=0 THEN
        RAISE EXCEPTION 'workbench_binding_invalid';
    END IF;
    IF w.state<>'retired' AND w.binding IS DISTINCT FROM p_binding THEN
        RAISE EXCEPTION 'workbench_binding_mismatch';
    END IF;
    IF w.state IN ('running','retained','cleanup_pending','pausing')
        OR (w.state IN ('allocating','resuming') AND w.operation_until>clock_timestamp()) THEN
        RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
    END IF;
    IF w.state='retired' AND w.resource IS NOT NULL THEN
        RAISE EXCEPTION 'workbench_cleanup_required';
    END IF;
    UPDATE public.cloud_workbenches SET
        state=CASE WHEN w.state='retired' THEN 'allocating' WHEN w.state='paused' THEN 'resuming' ELSE w.state END,
        generation=CASE WHEN w.state='retired' THEN gen_random_uuid() ELSE generation END,
        compute_epoch=CASE WHEN w.state IN ('retired','paused') THEN compute_epoch+1 ELSE compute_epoch END,
        binding=p_binding, operation_id=gen_random_uuid(), operation_until=clock_timestamp()+interval '180 seconds',
        version=version+1, updated_at=clock_timestamp()
        WHERE id=p_workbench RETURNING * INTO w;
    RETURN jsonb_build_object('acquired',true,'workbench',to_jsonb(w));
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_begin_open(
    p_workbench uuid,
    p_generation uuid,
    p_user uuid,
    p_project text,
    p_binding jsonb,
    p_deadline timestamptz
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; claimed jsonb; context jsonb;
BEGIN
    IF p_workbench IS NULL OR p_generation IS NULL OR p_user IS NULL OR p_project IS NULL
        OR p_deadline IS NULL THEN
        RAISE EXCEPTION 'workbench_open_claim_invalid' USING ERRCODE='22023';
    END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, true) THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF w.generation IS DISTINCT FROM p_generation OR w.open_deadline IS DISTINCT FROM p_deadline
        OR NOT w.open_requested OR w.open_deadline<=clock_timestamp() THEN
        RETURN jsonb_build_object('acquired',false,'workbench',to_jsonb(w));
    END IF;
    claimed := public.cloud_workbench_acquire(p_workbench,p_user,p_project,p_binding);
    IF NOT (claimed->>'acquired')::boolean THEN RETURN claimed; END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench;
    claimed := public.cloud_workbench_progress(w.id,w.generation,w.operation_id,
        CASE WHEN w.state='allocating' THEN 'allocating' ELSE 'connecting' END);
    context := public.cloud_workbench_context(p_user,p_project);
    RETURN jsonb_build_object('acquired',true,'workbench',claimed,
        'facts',context->'facts','revision',context->'revision');
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_request_open(
    p_workbench uuid,
    p_user uuid,
    p_project text,
    p_binding jsonb,
    p_revision jsonb
)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT public.cloud_workbench_request_open_in_pool(p_workbench,p_user,p_project,p_binding,p_revision,'default')
$$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_request_open_in_pool(
    p_workbench uuid,
    p_user uuid,
    p_project text,
    p_binding jsonb,
    p_revision jsonb,
    p_pool text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches; next_user uuid;
BEGIN
    IF coalesce(p_pool,'') !~ '^[a-z0-9][a-z0-9-]{0,62}$' THEN
        RAISE EXCEPTION 'workbench_pool_invalid' USING ERRCODE='22023';
    END IF;
    PERFORM public.authorization_assert_revision(p_project,p_revision);
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, true)
        OR NOT EXISTS(SELECT 1 FROM public.projects WHERE id=p_project AND lifecycle_status='ready') THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(p_binding) IS DISTINCT FROM 'object' OR coalesce(p_binding->>'provider','') !~ '^[a-z][a-z0-9_-]{0,62}$'
        OR length(coalesce(p_binding->>'artifact',''))=0 THEN RAISE EXCEPTION 'workbench_binding_invalid'; END IF;
    IF w.state<>'retired' AND w.binding IS DISTINCT FROM p_binding THEN RAISE EXCEPTION 'workbench_binding_mismatch'; END IF;
    IF w.state IN ('retained','cleanup_pending') THEN RETURN to_jsonb(w); END IF;
    IF (w.open_requested AND (w.open_deadline>clock_timestamp() OR w.operation_until>clock_timestamp())) OR w.state='running' THEN
        RETURN to_jsonb(w);
    END IF;
    next_user := CASE
        WHEN w.is_default AND w.agent_registration_id IS NULL
            AND NOT EXISTS(
                SELECT 1 FROM public.resolve_project_role(p_project, w.user_id) r
                WHERE r.effective_role IN ('admin', 'editor')
            ) THEN p_user
        ELSE w.user_id
    END;
    UPDATE public.cloud_workbenches SET user_id=next_user,open_requested=true,open_deadline=clock_timestamp()+interval '10 minutes',
        binding=p_binding,worker_pool=p_pool,operation_phase=CASE WHEN state='pausing' THEN 'pausing' ELSE 'queued' END,
        operation_error=NULL,operation_retryable=false,updated_at=clock_timestamp()
        WHERE id=w.id RETURNING * INTO w;
    RETURN to_jsonb(w);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_set_sandbox_settings(
    p_workbench uuid,
    p_user uuid,
    p_project text,
    p_settings jsonb,
    p_revision jsonb
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE w public.cloud_workbenches;
BEGIN
    PERFORM public.authorization_assert_revision(p_project,p_revision);
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=p_workbench FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, p_user, p_project, true) THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    UPDATE public.cloud_workbenches SET sandbox_settings=coalesce(p_settings,'{}'::jsonb),
        updated_at=clock_timestamp() WHERE id=w.id RETURNING * INTO w;
    RETURN to_jsonb(w);
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
    RETURN to_jsonb(c);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_attach_run() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE selected uuid := (NEW.policy->>'workbench_id')::uuid;
    session_workbench uuid; w public.cloud_workbenches;
BEGIN
    SELECT workbench_id INTO session_workbench FROM public.chat_sessions WHERE id=NEW.session_id FOR UPDATE;
    IF selected IS NULL THEN
        IF session_workbench IS NOT NULL OR NEW.workbench_id IS NOT NULL THEN
            RAISE EXCEPTION 'agent_workbench_required';
        END IF;
        RETURN NEW;
    END IF;
    IF session_workbench IS NOT NULL AND session_workbench<>selected THEN
        RAISE EXCEPTION 'agent_workbench_session_mismatch';
    END IF;
    IF session_workbench IS NULL AND EXISTS(SELECT 1 FROM public.agent_runs WHERE session_id=NEW.session_id) THEN
        RAISE EXCEPTION 'agent_workbench_session_mismatch';
    END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=selected FOR UPDATE;
    IF NOT FOUND OR NOT public._cloud_workbench_user_admitted(w, NEW.user_id, NEW.project_id, true) THEN
        RAISE EXCEPTION 'workbench_unavailable' USING ERRCODE='42501';
    END IF;
    UPDATE public.chat_sessions SET workbench_id=selected WHERE id=NEW.session_id;
    NEW.workbench_id := selected;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_task_acquire(
    p_run uuid,
    p_execution uuid,
    p_fence bigint,
    p_generation uuid
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r public.agent_runs; w public.cloud_workbenches; t public.cloud_workbench_tasks;
    status jsonb; resource_value jsonb; uid integer;
BEGIN
    IF p_run IS NULL OR p_execution IS NULL OR p_fence IS NULL OR p_generation IS NULL THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    r:=public.agent_run_assert(p_run,p_execution,p_fence);
    status:=public.agent_run_guard(p_run,p_execution,p_fence);
    IF status->>'code' IS NOT NULL THEN RAISE EXCEPTION 'workbench_task_admission_denied'; END IF;
    SELECT * INTO w FROM public.cloud_workbenches WHERE id=r.workbench_id FOR UPDATE;
    IF NOT FOUND OR w.generation IS DISTINCT FROM p_generation
        OR NOT public._cloud_workbench_user_admitted(w, r.user_id, r.project_id, true) THEN
        RAISE EXCEPTION 'workbench_task_binding_mismatch';
    END IF;
    IF w.state<>'running' OR w.resource IS NULL OR coalesce(w.resource->>'allocation_pending','true')<>'false' THEN
        RAISE EXCEPTION 'workbench_not_running';
    END IF;
    SELECT * INTO t FROM public.cloud_workbench_tasks WHERE execution_id=p_execution;
    IF FOUND THEN
        IF t.run_id<>p_run OR t.fence<>p_fence OR t.generation<>p_generation OR t.workbench_id<>w.id THEN
            RAISE EXCEPTION 'workbench_task_binding_mismatch';
        END IF;
        IF t.state<>'active' THEN RAISE EXCEPTION 'workbench_task_already_released'; END IF;
        RETURN to_jsonb(t);
    END IF;
    IF EXISTS(SELECT 1 FROM public.cloud_workbench_tasks WHERE run_id=p_run AND state='active') THEN
        RAISE EXCEPTION 'workbench_task_cleanup_required';
    END IF;
    uid:=public.cloud_workbench_task_uid(w);
    resource_value:=w.resource || jsonb_build_object('task_execution_id',p_execution,
        'task_uid',uid,'task_run_id',p_run,'task_fence',p_fence);
    INSERT INTO public.cloud_workbench_tasks(execution_id,workbench_id,generation,run_id,fence,task_uid,resource)
        VALUES(p_execution,w.id,p_generation,p_run,p_fence,uid,resource_value) RETURNING * INTO t;
    UPDATE public.cloud_workbenches SET next_task_uid=uid+1,
        last_activity_at=clock_timestamp(),unpublished_changes=true WHERE id=w.id;
    UPDATE public.agent_run_executions SET resource=resource_value WHERE id=p_execution AND run_id=p_run AND fence=p_fence;
    RETURN to_jsonb(t);
END $$;

CREATE OR REPLACE FUNCTION public.cloud_workbench_owner_deleted() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- Deleting the Project deletes all of its Workspaces. Deleting an individual
    -- user deletes only their personal non-default or external-Agent Workspaces,
    -- never the Project's shared default Workspace.
    UPDATE public.cloud_workbenches SET owner_deleted_at=clock_timestamp(),
        state=CASE WHEN resource IS NULL AND state='retired' THEN state ELSE 'retained' END,
        version=version+1, operation_id=gen_random_uuid(), updated_at=clock_timestamp()
    WHERE owner_deleted_at IS NULL AND (
        (TG_TABLE_NAME='projects' AND project_id=OLD.id::text) OR
        (TG_TABLE_NAME='users' AND user_id::text=OLD.id::text
            AND (NOT is_default OR agent_registration_id IS NOT NULL))
    );
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;

CREATE OR REPLACE FUNCTION public.access_workspace_connection_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE ticket public.workspace_ssh_grants; s public.access_sessions; g public.access_grants; context jsonb;
    owning_session uuid;
BEGIN
    IF TG_OP='UPDATE' THEN NEW.access_session_id:=OLD.access_session_id; END IF;
    owning_session:=coalesce(NEW.access_session_id,NEW.id);
    SELECT * INTO ticket FROM public.workspace_ssh_grants WHERE id=owning_session;
    IF ticket.access_grant_id IS NULL AND NEW.access_session_id IS NULL
        AND NOT EXISTS(SELECT 1 FROM public.access_sessions WHERE id=NEW.id) THEN RETURN NEW; END IF;
    NEW.access_session_id:=owning_session;
    IF TG_OP='UPDATE' AND NEW.state IN ('stopping','released') THEN
        IF owning_session=NEW.id THEN
            UPDATE public.access_sessions SET state=CASE NEW.state WHEN 'released' THEN 'closed' ELSE 'stopping' END,
                closed_at=CASE WHEN NEW.state='released' THEN coalesce(closed_at,clock_timestamp()) END
            WHERE id=NEW.id;
        END IF;
        RETURN NEW;
    END IF;
    IF ticket.access_grant_id IS NULL THEN RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
    SELECT * INTO g FROM public.access_grants WHERE id=ticket.access_grant_id;
    PERFORM 1 FROM public.access_registrations WHERE id=g.registration_id FOR SHARE;
    PERFORM 1 FROM public.access_grants WHERE id=g.id FOR SHARE;
    context:=public.access_grant_context(g.id,'ssh',owning_session);
    SELECT * INTO s FROM public.access_sessions WHERE id=owning_session FOR UPDATE;
    IF NOT FOUND OR s.state<>'active' OR s.lease_until<=clock_timestamp() OR ticket.revoked_at IS NOT NULL
        OR s.workbench_id<>NEW.workbench_id OR s.generation<>NEW.generation
        OR g.workbench_id<>NEW.workbench_id OR g.project_id<>NEW.project_id
        OR (g.approved_by<>NEW.user_id AND NOT EXISTS(
            SELECT 1 FROM public.cloud_workbenches w
            WHERE w.id=NEW.workbench_id AND w.is_default AND w.agent_registration_id IS NULL
        )) THEN
        RAISE EXCEPTION 'workspace_connection_expired' USING ERRCODE='42501'; END IF;
    NEW.lease_until:=least(NEW.lease_until,(context->'grant'->>'expires_at')::timestamptz);
    UPDATE public.access_sessions
    SET lease_until=CASE WHEN owning_session=NEW.id THEN NEW.lease_until ELSE greatest(lease_until,NEW.lease_until) END
    WHERE id=owning_session;
    RETURN NEW;
END $$;

COMMIT;
