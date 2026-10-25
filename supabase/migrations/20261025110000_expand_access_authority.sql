-- Durable access authority is independent of protocol credentials and VM leases.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE TABLE public.access_registrations (
 id uuid PRIMARY KEY,
 owner_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 owner_org_id text REFERENCES public.organizations(id) ON DELETE CASCADE,
 name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','suspended','revoked')),
 revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX access_registrations_owner ON public.access_registrations(owner_user_id,created_at,id);

CREATE TABLE public.access_registration_credentials (
 id uuid PRIMARY KEY,
 registration_id uuid NOT NULL REFERENCES public.access_registrations(id) ON DELETE CASCADE,
 key_hash text NOT NULL UNIQUE CHECK (length(key_hash)=64),
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','rotated','revoked')),
 expires_at timestamptz NOT NULL,
 request_id uuid,
 next_key_hash text,
 receipt jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (expires_at>created_at),
 CHECK (state<>'rotated' OR (request_id IS NOT NULL AND next_key_hash IS NOT NULL AND receipt IS NOT NULL)),
 CHECK (state<>'active' OR (request_id IS NULL AND next_key_hash IS NULL AND receipt IS NULL))
);
CREATE UNIQUE INDEX access_registration_one_current_key ON public.access_registration_credentials(registration_id)
 WHERE state='active';

CREATE FUNCTION public.access_authority_shape(p_target text,p_capabilities text[],p_channels text[])
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT p_capabilities IS NOT NULL AND p_channels IS NOT NULL
 AND cardinality(p_capabilities)>0 AND cardinality(p_channels)>0
 AND array_position(p_capabilities,NULL) IS NULL AND array_position(p_channels,NULL) IS NULL
 AND CASE p_target
 WHEN 'project_root' THEN p_capabilities <@ ARRAY['repository.read','repository.write']
   AND 'repository.read'=ANY(p_capabilities) AND p_channels <@ ARRAY['git','cli','mcp']
 WHEN 'workspace' THEN p_capabilities <@ ARRAY['workspace.read','workspace.write','workspace.shell']
   AND 'workspace.read'=ANY(p_capabilities) AND p_channels <@ ARRAY['ssh','mcp']
   AND (NOT ('workspace.shell'=ANY(p_capabilities)) OR 'workspace.write'=ANY(p_capabilities))
   AND (NOT ('ssh'=ANY(p_channels)) OR 'workspace.shell'=ANY(p_capabilities))
 ELSE false END
$$;

CREATE TABLE public.access_invitations (
 id uuid PRIMARY KEY,
 key_hash text NOT NULL UNIQUE CHECK (length(key_hash)=64),
 project_id text NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
 org_id text NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
 created_by uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 target_kind text NOT NULL,
 workbench_id uuid REFERENCES public.cloud_workbenches(id) ON DELETE CASCADE,
 capabilities text[] NOT NULL,
 channels text[] NOT NULL,
 recipient_user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 max_grant_seconds integer CHECK(max_grant_seconds BETWEEN 60 AND 31536000),
 expires_at timestamptz NOT NULL,
 max_approvals integer NOT NULL DEFAULT 1 CHECK(max_approvals BETWEEN 1 AND 1000),
 approvals integer NOT NULL DEFAULT 0 CHECK(approvals>=0 AND approvals<=max_approvals),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','revoked')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '30 days'),
 CHECK((target_kind='workspace')=(workbench_id IS NOT NULL)),
 CHECK(public.access_authority_shape(target_kind,capabilities,channels))
);
CREATE INDEX access_invitations_project ON public.access_invitations(project_id,created_at,id);

CREATE TABLE public.access_requests (
 id uuid PRIMARY KEY,
 invitation_id uuid NOT NULL REFERENCES public.access_invitations(id) ON DELETE CASCADE,
 registration_id uuid NOT NULL REFERENCES public.access_registrations(id) ON DELETE CASCADE,
 applicant_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected')),
 decided_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 decided_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(invitation_id,registration_id),
 CHECK((state='pending')=(decided_at IS NULL))
);

CREATE TABLE public.access_grants (
 id uuid PRIMARY KEY,
 request_id uuid UNIQUE REFERENCES public.access_requests(id) ON DELETE CASCADE,
 registration_id uuid NOT NULL REFERENCES public.access_registrations(id) ON DELETE CASCADE,
 project_id text NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
 org_id text NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
 approved_by uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 target_kind text NOT NULL,
 workbench_id uuid REFERENCES public.cloud_workbenches(id) ON DELETE CASCADE,
 capabilities text[] NOT NULL,
 channels text[] NOT NULL,
 starts_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz,
 single_session boolean NOT NULL DEFAULT false,
 consumed_by uuid,
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','suspended','revoked')),
 revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(expires_at IS NULL OR expires_at>starts_at),
 CHECK((target_kind='workspace')=(workbench_id IS NOT NULL)),
 CHECK(NOT single_session OR (target_kind='workspace' AND channels=ARRAY['ssh'])),
 CHECK(public.access_authority_shape(target_kind,capabilities,channels))
);
CREATE INDEX access_grants_registration ON public.access_grants(registration_id,project_id,id);
CREATE INDEX access_grants_project ON public.access_grants(project_id,created_at,id);

CREATE TABLE public.access_sessions (
 id uuid PRIMARY KEY,
 grant_id uuid NOT NULL REFERENCES public.access_grants(id) ON DELETE CASCADE,
 registration_id uuid NOT NULL REFERENCES public.access_registrations(id) ON DELETE CASCADE,
 channel text NOT NULL CHECK(channel IN ('ssh','mcp')),
 credential_id text NOT NULL,
 workbench_id uuid REFERENCES public.cloud_workbenches(id) ON DELETE CASCADE,
 generation uuid,
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','stopping','closed')),
 lease_until timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 closed_at timestamptz,
 CHECK((workbench_id IS NULL)=(generation IS NULL)),
 CHECK((state='closed')=(closed_at IS NOT NULL))
);
CREATE INDEX access_sessions_pending ON public.access_sessions(state,lease_until,id) WHERE state<>'closed';

CREATE TABLE public.access_lifecycle_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 registration_id uuid,
 grant_id uuid,
 session_id uuid,
 actor_user_id uuid,
 event text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 delivered_at timestamptz
);
CREATE INDEX access_lifecycle_events_pending ON public.access_lifecycle_events(id) WHERE delivered_at IS NULL;

ALTER TABLE public.access_surface_credentials ADD COLUMN access_grant_id uuid REFERENCES public.access_grants(id) ON DELETE CASCADE;
ALTER TABLE public.access_surface_credentials ADD COLUMN access_channel text CHECK(access_channel IN ('git','cli','mcp'));
ALTER TABLE public.access_surface_credentials ADD CONSTRAINT access_credential_parent_shape CHECK
 ((access_grant_id IS NULL)=(access_channel IS NULL));
ALTER TABLE public.workspace_ssh_grants ADD COLUMN access_grant_id uuid REFERENCES public.access_grants(id) ON DELETE CASCADE;
-- Keep the admission identity even if a registration is deleted. Losing a
-- parent must never turn a managed live connection into a legacy connection.
ALTER TABLE public.cloud_workbench_connections ADD COLUMN access_session_id uuid;
CREATE INDEX access_credentials_grant ON public.access_surface_credentials(access_grant_id) WHERE access_grant_id IS NOT NULL;
CREATE INDEX workspace_ssh_grants_parent ON public.workspace_ssh_grants(access_grant_id) WHERE access_grant_id IS NOT NULL;

DO $$ DECLARE relation text; BEGIN
 FOREACH relation IN ARRAY ARRAY['access_registrations','access_registration_credentials','access_invitations',
 'access_requests','access_grants','access_sessions','access_lifecycle_events'] LOOP
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',relation);
   EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated,service_role',relation);
   EXECUTE format('GRANT SELECT ON public.%I TO service_role',relation);
 END LOOP;
END $$;

-- Only the private Workspace's owner may delegate its native execution surface.
CREATE FUNCTION public.access_assert_owner(p_user uuid,p_project text,p_workbench uuid DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE authority record;
BEGIN
 SELECT * INTO authority FROM public.resolve_project_role(p_project,p_user);
 IF NOT FOUND OR authority.effective_role<>'admin' OR NOT EXISTS(
   SELECT 1 FROM public.projects WHERE id=p_project AND org_id=authority.org_id AND lifecycle_status='ready') THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 IF p_workbench IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.cloud_workbenches
   WHERE id=p_workbench AND project_id=p_project AND user_id=p_user AND is_default AND owner_deleted_at IS NULL) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 RETURN authority.org_id;
END $$;

-- One current snapshot used by every managed protocol. Raw records never imply permission.
CREATE FUNCTION public.access_grant_context(p_grant uuid,p_channel text,p_session uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE g public.access_grants; r public.access_registrations; organization text;
BEGIN
 SELECT * INTO g FROM public.access_grants WHERE id=p_grant;
 IF NOT FOUND THEN RAISE EXCEPTION 'access_denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.access_registrations WHERE id=g.registration_id;
 IF NOT FOUND OR p_channel IS NULL OR r.state<>'active' OR g.state<>'active' OR NOT(p_channel=ANY(g.channels))
   OR g.starts_at>clock_timestamp() OR (g.expires_at IS NOT NULL AND g.expires_at<=clock_timestamp())
   OR (g.single_session AND g.consumed_by IS NOT NULL AND g.consumed_by IS DISTINCT FROM p_session) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 organization := public.access_assert_owner(g.approved_by,g.project_id,g.workbench_id);
 IF organization IS DISTINCT FROM g.org_id OR (r.owner_org_id IS NOT NULL AND r.owner_org_id<>g.org_id) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 IF r.owner_org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members
   WHERE org_id=r.owner_org_id AND user_id=r.owner_user_id) THEN
   RAISE EXCEPTION 'access_denied' USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object('grant',to_jsonb(g),'registration',to_jsonb(r),
   'authority',public.authorization_project_facts(g.project_id,g.approved_by),'checked_at',clock_timestamp());
END $$;

-- Used by the repository's final publish check. Revocation does not need to lock
-- child credentials, so holding these rows until commit does not invert its lock order.
CREATE FUNCTION public.access_credential_grant_valid(p_id text,p_channel text,p_write boolean DEFAULT false)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c public.access_surface_credentials; g public.access_grants; context jsonb;
BEGIN
 SELECT * INTO c FROM public.access_surface_credentials WHERE id=p_id;
 IF NOT FOUND THEN RETURN false; END IF;
 IF c.access_grant_id IS NULL THEN RETURN true; END IF;
 IF c.access_channel IS DISTINCT FROM p_channel THEN RETURN false; END IF;
 SELECT * INTO g FROM public.access_grants WHERE id=c.access_grant_id;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM public.access_registrations WHERE id=g.registration_id FOR SHARE;
 PERFORM 1 FROM public.access_grants WHERE id=g.id FOR SHARE;
 context := public.access_grant_context(g.id,p_channel);
 g := jsonb_populate_record(NULL::public.access_grants,context->'grant');
 RETURN g.project_id=c.project_id AND g.org_id=c.org_id AND g.target_kind='project_root'
   AND 'repository.read'=ANY(g.capabilities) AND (NOT p_write OR 'repository.write'=ANY(g.capabilities));
EXCEPTION WHEN insufficient_privilege THEN RETURN false;
END $$;

REVOKE ALL ON FUNCTION public.access_authority_shape(text,text[],text[]),
 public.access_assert_owner(uuid,text,uuid),public.access_grant_context(uuid,text,uuid),
 public.access_credential_grant_valid(text,text,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.access_grant_context(uuid,text,uuid) TO service_role;
COMMIT;
