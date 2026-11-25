-- Additive: the project's Cloud Agent in Telegram, Slack and WeCom chats.
-- Only adds objects; no existing table, function or row is changed. The one
-- rewritten object is the read-only view context_activity_items, which keeps
-- its columns, types and order and gains a fourth kind, 'chat_app'.
-- 1. Eight service-only tables (RLS on, no client grants).
-- 2. chat_app_command: people (start a link, connect, allow, change, take over,
--    remove). Every action re-checks the project role and records an event.
-- 3. chat_app_gateway_command: processes (intake, leases with fences, replies,
--    WeCom connection ownership). Rows are claimed with SKIP LOCKED.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE TABLE public.chat_app_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id text NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('slack','wecom')),
    external_id text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    secret text CHECK (secret IS NULL OR length(secret) BETWEEN 1 AND 8192),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','active','failed','displaced','revoked')),
    created_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
    lease_owner text,
    lease_expires_at timestamptz,
    fence bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (platform, external_id)
);

CREATE TABLE public.chat_app_conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id text NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    org_id text NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    platform text NOT NULL CHECK (platform IN ('telegram','slack','wecom')),
    connection_id uuid REFERENCES public.chat_app_connections(id) ON DELETE CASCADE,
    external_chat_id text NOT NULL CHECK (length(external_chat_id) BETWEEN 1 AND 200),
    chat_kind text NOT NULL CHECK (chat_kind IN ('group','dm')),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 64),
    knock_hint text CHECK (knock_hint IS NULL OR length(knock_hint) <= 16),
    state text NOT NULL DEFAULT 'asking' CHECK (state IN ('asking','active','denied','removed')),
    enabled boolean NOT NULL DEFAULT true,
    owner_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    owner_external_id text,
    who_can_ask text NOT NULL DEFAULT 'owner' CHECK (who_can_ask IN ('owner','project','anyone')),
    daily_limit_units integer DEFAULT 60 CHECK (daily_limit_units IS NULL OR daily_limit_units > 0),
    stopped_reason text CHECK (stopped_reason IS NULL OR stopped_reason IN ('owner_left','removed_there')),
    link_id uuid,
    decided_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((platform = 'telegram') = (connection_id IS NULL))
);
-- A chat is connected to at most one project at a time.
CREATE UNIQUE INDEX chat_app_conversations_live_chat
    ON public.chat_app_conversations (platform, connection_id, external_chat_id) NULLS NOT DISTINCT
    WHERE state IN ('asking','active');
CREATE INDEX chat_app_conversations_project ON public.chat_app_conversations (project_id, state);

CREATE TABLE public.chat_app_threads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES public.chat_app_conversations(id) ON DELETE CASCADE,
    external_thread_key text NOT NULL CHECK (length(external_thread_key) BETWEEN 1 AND 300),
    agent_session_id text,
    last_activity_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (conversation_id, external_thread_key)
);

CREATE TABLE public.chat_app_inbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform text NOT NULL CHECK (platform IN ('telegram','slack','wecom')),
    connection_id uuid REFERENCES public.chat_app_connections(id) ON DELETE CASCADE,
    message_key text NOT NULL CHECK (length(message_key) BETWEEN 1 AND 300),
    external_event_id text,
    via text NOT NULL CHECK (via IN ('message','guest','action','membership')),
    kind text NOT NULL CHECK (kind IN ('question','knock','stop','joined','left','new_thread','uninstalled')),
    external_chat_id text NOT NULL,
    external_thread_key text,
    asker_external_id text,
    asker_label text,
    payload jsonb NOT NULL DEFAULT '{}',
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','waiting','held','done')),
    held_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_expires_at timestamptz,
    fence bigint NOT NULL DEFAULT 0,
    conversation_id uuid REFERENCES public.chat_app_conversations(id) ON DELETE SET NULL,
    thread_id uuid REFERENCES public.chat_app_threads(id) ON DELETE SET NULL,
    request_id uuid,
    run_id uuid,
    outcome text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    UNIQUE NULLS NOT DISTINCT (platform, connection_id, message_key)
);
CREATE INDEX chat_app_inbox_claim ON public.chat_app_inbox (created_at)
    WHERE state IN ('pending','waiting','held');
CREATE INDEX chat_app_inbox_thread ON public.chat_app_inbox (thread_id, created_at)
    WHERE state IN ('pending','waiting');

CREATE TABLE public.chat_app_replies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES public.chat_app_conversations(id) ON DELETE CASCADE,
    thread_id uuid REFERENCES public.chat_app_threads(id) ON DELETE SET NULL,
    inbox_id uuid REFERENCES public.chat_app_inbox(id) ON DELETE SET NULL,
    run_id uuid,
    notice_code text,
    payload jsonb NOT NULL DEFAULT '{}',
    external_message_id text,
    inline_message_id text,
    stream_id text,
    stream_started_at timestamptz,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','active','done','abandoned')),
    rendered_digest text,
    done_notice_sent boolean NOT NULL DEFAULT false,
    units integer NOT NULL DEFAULT 0 CHECK (units >= 0),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    lease_owner text,
    lease_expires_at timestamptz,
    fence bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    CHECK (run_id IS NOT NULL OR notice_code IS NOT NULL)
);
CREATE UNIQUE INDEX chat_app_replies_inbox ON public.chat_app_replies (inbox_id) WHERE inbox_id IS NOT NULL;
CREATE INDEX chat_app_replies_claim ON public.chat_app_replies (next_attempt_at, created_at)
    WHERE state IN ('pending','active');
CREATE INDEX chat_app_replies_usage ON public.chat_app_replies (conversation_id, created_at);

CREATE TABLE public.chat_app_link_codes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    purpose text NOT NULL CHECK (purpose IN ('knock','slack_install','slack_wait','identity')),
    code_hash text NOT NULL UNIQUE CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    platform text NOT NULL CHECK (platform IN ('telegram','slack','wecom')),
    project_id text REFERENCES public.projects(id) ON DELETE CASCADE,
    user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
    connection_id uuid REFERENCES public.chat_app_connections(id) ON DELETE CASCADE,
    scope text,
    external_user_id text,
    conversation_id uuid REFERENCES public.chat_app_conversations(id) ON DELETE SET NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (purpose = 'identity' OR (project_id IS NOT NULL AND user_id IS NOT NULL)),
    CHECK (purpose <> 'identity' OR external_user_id IS NOT NULL)
);

CREATE TABLE public.chat_app_identities (
    platform text NOT NULL CHECK (platform IN ('telegram','slack','wecom')),
    scope text NOT NULL,
    external_user_id text NOT NULL,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (platform, scope, external_user_id)
);

CREATE TABLE public.chat_app_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id text NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    conversation_id uuid REFERENCES public.chat_app_conversations(id) ON DELETE SET NULL,
    actor_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,
    action text NOT NULL CHECK (action IN
        ('added','allowed','denied','updated','taken_over','removed','removed_there')),
    label text NOT NULL,
    platform text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX chat_app_events_project ON public.chat_app_events (project_id, created_at DESC);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['chat_app_connections','chat_app_conversations','chat_app_threads',
    'chat_app_inbox','chat_app_replies','chat_app_link_codes','chat_app_identities','chat_app_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, anon, authenticated, service_role', t);
    EXECUTE format('GRANT SELECT ON public.%I TO service_role', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO service_role USING (true)', t || '_service', t);
  END LOOP;
END $$;

-- The human's role in the project, or NULL.
CREATE FUNCTION public._chat_app_role(p_project text, p_user uuid)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT r.effective_role FROM public.resolve_project_role(p_project, p_user) r
  JOIN public.projects p ON p.id = p_project AND p.org_id = r.org_id AND p.lifecycle_status = 'ready'
  LIMIT 1
$$;

CREATE FUNCTION public._chat_app_can_run(p_project text, p_user uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT coalesce(public._chat_app_role(p_project, p_user) IN ('admin','editor'), false)
$$;

CREATE FUNCTION public._chat_app_used_today(p_conversation uuid)
RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT coalesce(sum(units), 0)::integer FROM public.chat_app_replies
  WHERE conversation_id = p_conversation
    AND created_at >= date_trunc('day', clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
$$;

CREATE FUNCTION public._chat_app_event(c public.chat_app_conversations, p_actor uuid, p_action text,
    p_detail jsonb DEFAULT '{}')
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  INSERT INTO public.chat_app_events (project_id, conversation_id, actor_user_id, action, label, platform, detail)
  VALUES (c.project_id, c.id, p_actor, p_action, c.display_name, c.platform, coalesce(p_detail, '{}'))
$$;

-- What a client may see of a chat. Never a secret.
CREATE FUNCTION public._chat_app_conversation_json(c public.chat_app_conversations)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT jsonb_build_object(
    'id', c.id, 'project_id', c.project_id, 'platform', c.platform, 'connection_id', c.connection_id,
    'chat_kind', c.chat_kind, 'display_name', c.display_name, 'knock_hint', c.knock_hint,
    'state', c.state, 'enabled', c.enabled, 'owner_user_id', c.owner_user_id,
    'owner_name', (SELECT coalesce(nullif(p.display_name, ''), p.email) FROM public.profiles p
                   WHERE p.user_id = c.owner_user_id),
    'who_can_ask', c.who_can_ask, 'daily_limit_units', c.daily_limit_units,
    'stopped_reason', c.stopped_reason, 'used_today_units', public._chat_app_used_today(c.id),
    'working', EXISTS (SELECT 1 FROM public.chat_app_replies r
                       WHERE r.conversation_id = c.id AND r.state = 'active' AND r.run_id IS NOT NULL),
    'last_activity_at', (SELECT max(t.last_activity_at) FROM public.chat_app_threads t
                         WHERE t.conversation_id = c.id),
    'workspace_name', (SELECT x.display_name FROM public.chat_app_connections x WHERE x.id = c.connection_id),
    -- The space ran out of Agent hours on this chat's latest question (banner).
    'no_hours', coalesce((SELECT q.outcome = 'refused:runtime_credit_unavailable'
                          FROM public.chat_app_inbox q
                          WHERE q.conversation_id = c.id AND q.kind = 'question' AND q.state = 'done'
                          ORDER BY q.finished_at DESC NULLS LAST LIMIT 1), false),
    'created_at', c.created_at)
$$;

CREATE FUNCTION public._chat_app_connection_json(x public.chat_app_connections, p_user uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT jsonb_build_object('id', x.id, 'platform', x.platform, 'external_id', x.external_id,
    'display_name', x.display_name, 'status', x.status, 'created_at', x.created_at,
    'linked_me', EXISTS (SELECT 1 FROM public.chat_app_identities i
                         WHERE i.platform = x.platform AND i.scope = x.external_id AND i.user_id = p_user))
$$;

-- The owner who can no longer run Agents stops the chat; checked whenever a
-- chat is read for a manager or asked in.
CREATE FUNCTION public._chat_app_check_owner(c public.chat_app_conversations)
RETURNS public.chat_app_conversations LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
  IF c.state = 'active' AND c.stopped_reason IS NULL AND NOT public._chat_app_can_run(c.project_id, c.owner_user_id) THEN
    UPDATE public.chat_app_conversations SET stopped_reason = 'owner_left', updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
  END IF;
  RETURN c;
END $$;

CREATE FUNCTION public._chat_app_link_json(l public.chat_app_link_codes)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT jsonb_build_object('id', l.id, 'purpose', l.purpose, 'platform', l.platform,
    'connection_id', l.connection_id, 'expires_at', l.expires_at,
    'state', CASE
      WHEN l.conversation_id IS NOT NULL THEN 'knocked'
      WHEN l.consumed_at IS NOT NULL THEN 'done'
      WHEN l.expires_at <= clock_timestamp() THEN 'expired'
      ELSE 'waiting' END,
    'conversation', (SELECT public._chat_app_conversation_json(c) FROM public.chat_app_conversations c
                     WHERE c.id = l.conversation_id),
    'connection', (SELECT public._chat_app_connection_json(x, l.user_id) FROM public.chat_app_connections x
                   WHERE x.id = l.connection_id))
$$;

-- People. p_user is the signed-in person; the route already checked the
-- project action and this function checks the role again.
CREATE FUNCTION public.chat_app_command(p_action text, p_user uuid, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
  project text := p_payload->>'project_id';
  role text;
  organization text;
  c public.chat_app_conversations;
  l public.chat_app_link_codes;
  x public.chat_app_connections;
  platform_name text := p_payload->>'platform';
  changed jsonb := '{}';
BEGIN
  IF p_user IS NULL OR NOT EXISTS (SELECT 1 FROM auth.users WHERE id = p_user) THEN
    RAISE EXCEPTION 'chat_app_denied' USING ERRCODE = '42501';
  END IF;

  IF p_action = 'link_identity' THEN
    SELECT * INTO l FROM public.chat_app_link_codes
    WHERE code_hash = p_payload->>'code_hash' AND purpose = 'identity' FOR UPDATE;
    IF NOT FOUND OR l.consumed_at IS NOT NULL OR l.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'chat_app_link_expired' USING ERRCODE = '55000';
    END IF;
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp(), user_id = p_user WHERE id = l.id;
    INSERT INTO public.chat_app_identities (platform, scope, external_user_id, user_id)
    VALUES (l.platform, l.scope, l.external_user_id, p_user)
    ON CONFLICT (platform, scope, external_user_id) DO UPDATE SET user_id = EXCLUDED.user_id;
    -- The question asked just before linking starts now.
    UPDATE public.chat_app_inbox i SET state = 'pending', held_until = NULL
    WHERE i.state = 'held' AND i.platform = l.platform AND i.asker_external_id = l.external_user_id
      AND i.held_until > clock_timestamp();
    RETURN jsonb_build_object('platform', l.platform, 'linked', true);
  END IF;

  role := public._chat_app_role(project, p_user);
  IF role IS NULL THEN
    RAISE EXCEPTION 'chat_app_denied' USING ERRCODE = '42501';
  END IF;
  organization := (SELECT org_id FROM public.projects WHERE id = project);

  IF p_action = 'list' THEN
    FOR c IN SELECT * FROM public.chat_app_conversations
             WHERE project_id = project AND state = 'active' FOR UPDATE LOOP
      PERFORM public._chat_app_check_owner(c);
    END LOOP;
    RETURN jsonb_build_object(
      'can_manage', role = 'admin',
      'conversations', coalesce((SELECT jsonb_agg(public._chat_app_conversation_json(c2) ORDER BY c2.state, c2.created_at)
        FROM public.chat_app_conversations c2 WHERE c2.project_id = project AND c2.state IN ('asking','active')), '[]'),
      'connections', CASE WHEN role = 'admin' THEN coalesce((SELECT jsonb_agg(public._chat_app_connection_json(x2, p_user)
        ORDER BY x2.created_at) FROM public.chat_app_connections x2
        WHERE x2.org_id = organization AND x2.status <> 'revoked'), '[]') ELSE '[]' END);
  END IF;

  IF role <> 'admin' THEN
    RAISE EXCEPTION 'chat_app_denied' USING ERRCODE = '42501';
  END IF;

  IF p_action = 'start_link' THEN
    IF platform_name IS NULL OR p_payload->>'purpose' NOT IN ('knock','slack_install','slack_wait')
       OR (p_payload->>'purpose' = 'knock') <> (platform_name IN ('telegram','wecom'))
       OR coalesce(p_payload->>'code_hash', '') !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    IF p_payload->>'purpose' = 'slack_wait' OR platform_name = 'wecom' THEN
      SELECT * INTO x FROM public.chat_app_connections
      WHERE id = (p_payload->>'connection_id')::uuid AND org_id = organization AND platform = platform_name;
      IF NOT FOUND OR x.status <> 'active' THEN
        RAISE EXCEPTION 'chat_app_connection_not_ready' USING ERRCODE = '55000';
      END IF;
    END IF;
    -- A new wait replaces the previous one from the same person and app.
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp()
    WHERE project_id = project AND user_id = p_user AND platform = platform_name
      AND purpose = p_payload->>'purpose' AND consumed_at IS NULL;
    INSERT INTO public.chat_app_link_codes (purpose, code_hash, platform, project_id, user_id, connection_id, expires_at)
    VALUES (p_payload->>'purpose', p_payload->>'code_hash', platform_name, project, p_user, x.id,
            clock_timestamp() + interval '10 minutes')
    RETURNING * INTO l;
    RETURN public._chat_app_link_json(l);
  ELSIF p_action = 'link_status' THEN
    SELECT * INTO l FROM public.chat_app_link_codes
    WHERE id = (p_payload->>'link_id')::uuid AND project_id = project AND user_id = p_user;
    IF NOT FOUND THEN RAISE EXCEPTION 'chat_app_not_found' USING ERRCODE = '42501'; END IF;
    RETURN public._chat_app_link_json(l);
  ELSIF p_action = 'connect' THEN
    IF platform_name <> 'wecom' OR coalesce(p_payload->>'external_id', '') = ''
       OR coalesce(p_payload->>'secret', '') = '' THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO x FROM public.chat_app_connections
    WHERE platform = 'wecom' AND external_id = p_payload->>'external_id' FOR UPDATE;
    IF FOUND AND x.org_id <> organization AND x.status <> 'revoked' THEN
      RAISE EXCEPTION 'chat_app_connection_taken' USING ERRCODE = '55000';
    END IF;
    INSERT INTO public.chat_app_connections (org_id, platform, external_id, display_name, secret, status, created_by)
    VALUES (organization, 'wecom', p_payload->>'external_id', coalesce(nullif(p_payload->>'display_name', ''), 'WeCom bot'),
            p_payload->>'secret', 'pending', p_user)
    ON CONFLICT (platform, external_id) DO UPDATE SET org_id = EXCLUDED.org_id, secret = EXCLUDED.secret,
      status = 'pending', created_by = EXCLUDED.created_by, lease_owner = NULL, lease_expires_at = NULL,
      updated_at = clock_timestamp()
    RETURNING * INTO x;
    RETURN public._chat_app_connection_json(x, p_user);
  END IF;

  SELECT * INTO c FROM public.chat_app_conversations
  WHERE id = (p_payload->>'conversation_id')::uuid AND project_id = project FOR UPDATE;
  IF NOT FOUND OR c.state IN ('denied','removed') THEN
    RAISE EXCEPTION 'chat_app_not_found' USING ERRCODE = '42501';
  END IF;

  IF p_action = 'decide' THEN
    IF p_payload->>'decision' NOT IN ('allow','deny') THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    IF c.state <> 'asking' THEN RAISE EXCEPTION 'chat_app_conflict' USING ERRCODE = '55000'; END IF;
    UPDATE public.chat_app_conversations SET
      state = CASE p_payload->>'decision' WHEN 'allow' THEN 'active' ELSE 'denied' END,
      decided_by = p_user, decided_at = clock_timestamp(), updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
    PERFORM public._chat_app_event(c, p_user, CASE c.state WHEN 'active' THEN 'allowed' ELSE 'denied' END);
    IF c.state = 'active' THEN
      INSERT INTO public.chat_app_replies (conversation_id, notice_code) VALUES (c.id, 'welcome');
    END IF;
  ELSIF p_action = 'update' THEN
    IF c.state <> 'active' OR c.stopped_reason IS NOT NULL THEN
      RAISE EXCEPTION 'chat_app_conflict' USING ERRCODE = '55000';
    END IF;
    IF p_payload ? 'enabled' THEN
      IF jsonb_typeof(p_payload->'enabled') <> 'boolean' THEN
        RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023'; END IF;
      c.enabled := (p_payload->>'enabled')::boolean; changed := changed || '{"enabled":true}';
    END IF;
    IF p_payload ? 'who_can_ask' THEN
      IF p_payload->>'who_can_ask' NOT IN ('owner','project','anyone') THEN
        RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023'; END IF;
      c.who_can_ask := p_payload->>'who_can_ask'; changed := changed || '{"who_can_ask":true}';
    END IF;
    IF p_payload ? 'daily_limit_units' THEN
      IF jsonb_typeof(p_payload->'daily_limit_units') NOT IN ('number','null')
         OR (p_payload->>'daily_limit_units')::integer <= 0 THEN
        RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023'; END IF;
      c.daily_limit_units := (p_payload->>'daily_limit_units')::integer;
      changed := changed || '{"daily_limit_units":true}';
    END IF;
    IF p_payload ? 'display_name' THEN
      IF length(btrim(coalesce(p_payload->>'display_name', ''))) NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023'; END IF;
      c.display_name := btrim(p_payload->>'display_name'); changed := changed || '{"display_name":true}';
    END IF;
    IF changed = '{}' THEN RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023'; END IF;
    UPDATE public.chat_app_conversations SET enabled = c.enabled, who_can_ask = c.who_can_ask,
      daily_limit_units = c.daily_limit_units, display_name = c.display_name, updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
    PERFORM public._chat_app_event(c, p_user, 'updated', jsonb_build_object('changed', changed));
  ELSIF p_action = 'take_over' THEN
    IF c.state <> 'active' OR c.stopped_reason IS DISTINCT FROM 'owner_left' THEN
      RAISE EXCEPTION 'chat_app_conflict' USING ERRCODE = '55000';
    END IF;
    UPDATE public.chat_app_conversations SET owner_user_id = p_user, stopped_reason = NULL,
      owner_external_id = (SELECT i.external_user_id FROM public.chat_app_identities i
        WHERE i.platform = c.platform AND i.user_id = p_user
          AND i.scope = coalesce((SELECT external_id FROM public.chat_app_connections WHERE id = c.connection_id), 'telegram')
        LIMIT 1),
      updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
    PERFORM public._chat_app_event(c, p_user, 'taken_over');
  ELSIF p_action = 'remove' THEN
    UPDATE public.chat_app_conversations SET state = 'removed', updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
    PERFORM public._chat_app_event(c, p_user, 'removed');
    UPDATE public.chat_app_inbox SET state = 'done', outcome = 'removed', finished_at = clock_timestamp(),
      lease_owner = NULL, lease_expires_at = NULL
    WHERE conversation_id = c.id AND state IN ('pending','waiting','held');
    IF c.stopped_reason IS DISTINCT FROM 'removed_there' THEN
      INSERT INTO public.chat_app_replies (conversation_id, notice_code) VALUES (c.id, 'removed');
    END IF;
    RETURN public._chat_app_conversation_json(c) || jsonb_build_object('stop_runs',
      coalesce((SELECT jsonb_agg(r.run_id) FROM public.chat_app_replies r
                WHERE r.conversation_id = c.id AND r.state = 'active' AND r.run_id IS NOT NULL), '[]'));
  ELSE
    RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
  END IF;
  RETURN public._chat_app_conversation_json(c);
END $$;

-- Processes. p_worker names the instance holding a lease.
CREATE FUNCTION public.chat_app_gateway_command(p_action text, p_worker text, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
  i public.chat_app_inbox;
  r public.chat_app_replies;
  c public.chat_app_conversations;
  t public.chat_app_threads;
  l public.chat_app_link_codes;
  x public.chat_app_connections;
  lease interval := make_interval(secs => coalesce((p_payload->>'lease_seconds')::integer, 30));
  connection uuid := nullif(p_payload->>'connection_id', '')::uuid;
  platform_name text := p_payload->>'platform';
  asker_user uuid;
  identity_scope text;
BEGIN
  IF coalesce(p_worker, '') = '' THEN
    RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
  END IF;

  IF p_action = 'receive' THEN
    INSERT INTO public.chat_app_inbox (platform, connection_id, message_key, external_event_id, via, kind,
      external_chat_id, external_thread_key, asker_external_id, asker_label, payload)
    VALUES (platform_name, connection, p_payload->>'message_key', p_payload->>'external_event_id',
      p_payload->>'via', p_payload->>'kind', p_payload->>'external_chat_id', p_payload->>'external_thread_key',
      p_payload->>'asker_external_id', left(p_payload->>'asker_label', 200), coalesce(p_payload->'payload', '{}'))
    ON CONFLICT (platform, connection_id, message_key) DO NOTHING
    RETURNING * INTO i;
    RETURN jsonb_build_object('id', i.id, 'duplicate', i.id IS NULL);

  ELSIF p_action = 'claim_inbox' THEN
    -- Questions held for an account link give up after their window.
    UPDATE public.chat_app_inbox SET state = 'done', outcome = 'link_expired', finished_at = clock_timestamp()
    WHERE state = 'held' AND held_until <= clock_timestamp();
    SELECT * INTO i FROM public.chat_app_inbox q
    WHERE q.state IN ('pending','waiting')
      AND (q.lease_expires_at IS NULL OR q.lease_expires_at <= clock_timestamp())
      AND (q.thread_id IS NULL OR (
        NOT EXISTS (SELECT 1 FROM public.chat_app_replies a WHERE a.thread_id = q.thread_id
                    AND a.state = 'active' AND a.run_id IS NOT NULL)
        AND NOT EXISTS (SELECT 1 FROM public.chat_app_inbox e WHERE e.thread_id = q.thread_id
                        AND e.state = 'waiting' AND e.created_at < q.created_at)))
    ORDER BY q.created_at
    FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE public.chat_app_inbox SET lease_owner = p_worker, lease_expires_at = clock_timestamp() + lease,
      fence = fence + 1, attempts = attempts + 1
    WHERE id = i.id RETURNING * INTO i;
    RETURN to_jsonb(i);

  ELSIF p_action = 'context' THEN
    -- Everything dispatch needs to decide, read in one place.
    SELECT * INTO c FROM public.chat_app_conversations
    WHERE platform = platform_name AND connection_id IS NOT DISTINCT FROM connection
      AND external_chat_id = p_payload->>'external_chat_id' AND state IN ('asking','active');
    IF FOUND THEN c := public._chat_app_check_owner(c); END IF;
    identity_scope := coalesce((SELECT external_id FROM public.chat_app_connections WHERE id = connection), 'telegram');
    SELECT user_id INTO asker_user FROM public.chat_app_identities
    WHERE platform = platform_name AND scope = identity_scope AND external_user_id = p_payload->>'asker_external_id';
    IF c.id IS NOT NULL AND p_payload->>'external_thread_key' IS NOT NULL THEN
      SELECT * INTO t FROM public.chat_app_threads
      WHERE conversation_id = c.id AND external_thread_key = p_payload->>'external_thread_key';
      IF NOT FOUND THEN
        -- A reply to one of our messages continues that message's thread.
        SELECT th.* INTO t FROM public.chat_app_threads th
        JOIN public.chat_app_replies rp ON rp.thread_id = th.id
        WHERE th.conversation_id = c.id AND rp.external_message_id = p_payload->>'external_thread_key'
        LIMIT 1;
      END IF;
    END IF;
    RETURN jsonb_build_object(
      'conversation', CASE WHEN c.id IS NULL THEN NULL ELSE to_jsonb(c) || jsonb_build_object(
        'org_id', c.org_id, 'owner_name', (SELECT coalesce(nullif(p.display_name, ''), p.email)
          FROM public.profiles p WHERE p.user_id = c.owner_user_id),
        'workspace_name', (SELECT display_name FROM public.chat_app_connections WHERE id = c.connection_id)) END,
      'identity_scope', identity_scope,
      -- Slack may not say who invited it; the next mention within 10 minutes decides.
      'join_without_inviter', EXISTS (SELECT 1 FROM public.chat_app_inbox j
        WHERE j.platform = platform_name AND j.connection_id IS NOT DISTINCT FROM connection
          AND j.external_chat_id = p_payload->>'external_chat_id' AND j.kind = 'joined'
          AND j.outcome = 'inviter_missing' AND j.created_at > clock_timestamp() - interval '10 minutes'),
      'asker_user_id', asker_user,
      'asker_can_run', CASE WHEN asker_user IS NULL OR c.id IS NULL THEN NULL
                            ELSE public._chat_app_can_run(c.project_id, asker_user) END,
      'used_today_units', CASE WHEN c.id IS NULL THEN 0 ELSE public._chat_app_used_today(c.id) END,
      -- Paused / limit / "only X can ask" are said to one person once an hour.
      'notified_last_hour', coalesce((SELECT jsonb_agg(DISTINCT q.outcome) FROM public.chat_app_inbox q
        WHERE q.conversation_id = c.id AND q.asker_external_id = p_payload->>'asker_external_id'
          AND q.finished_at > clock_timestamp() - interval '1 hour'
          AND q.outcome IN ('paused','limit','only_owner','not_in_project')), '[]'),
      'asked_last_hour', (SELECT count(*) FROM public.chat_app_inbox q WHERE q.conversation_id = c.id
                          AND q.kind = 'question' AND q.created_at > clock_timestamp() - interval '1 hour'),
      'thread', CASE WHEN t.id IS NULL THEN NULL ELSE to_jsonb(t) || jsonb_build_object(
        'busy', EXISTS (SELECT 1 FROM public.chat_app_replies a WHERE a.thread_id = t.id
                        AND a.state = 'active' AND a.run_id IS NOT NULL),
        'waiting', (SELECT count(*) FROM public.chat_app_inbox w WHERE w.thread_id = t.id
                    AND w.state = 'waiting' AND w.id IS DISTINCT FROM (p_payload->>'inbox_id')::uuid)) END);

  ELSIF p_action = 'knock' THEN
    IF platform_name = 'slack' THEN
      SELECT * INTO l FROM public.chat_app_link_codes k
      WHERE k.purpose = 'slack_wait' AND k.connection_id = connection AND k.consumed_at IS NULL
        AND k.expires_at > clock_timestamp()
        AND k.user_id = (SELECT user_id FROM public.chat_app_identities
          WHERE platform = 'slack' AND scope = (SELECT external_id FROM public.chat_app_connections WHERE id = connection)
            AND external_user_id = p_payload->>'owner_external_id')
      ORDER BY k.created_at DESC LIMIT 1 FOR UPDATE;
    ELSE
      SELECT * INTO l FROM public.chat_app_link_codes
      WHERE code_hash = p_payload->>'code_hash' AND purpose = 'knock' AND platform = platform_name
        AND connection_id IS NOT DISTINCT FROM connection
      FOR UPDATE;
    END IF;
    IF NOT FOUND OR l.consumed_at IS NOT NULL OR l.expires_at <= clock_timestamp() THEN
      RETURN jsonb_build_object('knocked', false);
    END IF;
    SELECT * INTO c FROM public.chat_app_conversations
    WHERE platform = platform_name AND connection_id IS NOT DISTINCT FROM connection
      AND external_chat_id = p_payload->>'external_chat_id' AND state IN ('asking','active');
    IF FOUND THEN
      RETURN jsonb_build_object('knocked', false, 'reason', 'already_connected');
    END IF;
    INSERT INTO public.chat_app_conversations (project_id, org_id, platform, connection_id, external_chat_id,
      chat_kind, display_name, knock_hint, owner_user_id, owner_external_id, link_id)
    VALUES (l.project_id, (SELECT org_id FROM public.projects WHERE id = l.project_id), platform_name, connection,
      p_payload->>'external_chat_id', p_payload->>'chat_kind',
      left(coalesce(nullif(btrim(p_payload->>'display_name'), ''), 'Chat'), 64),
      p_payload->>'knock_hint', l.user_id, p_payload->>'owner_external_id', l.id)
    RETURNING * INTO c;
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp(), conversation_id = c.id WHERE id = l.id;
    -- The person who knocked with the code is the owner on that platform.
    IF p_payload->>'owner_external_id' IS NOT NULL THEN
      INSERT INTO public.chat_app_identities (platform, scope, external_user_id, user_id)
      VALUES (platform_name, coalesce((SELECT external_id FROM public.chat_app_connections WHERE id = connection), 'telegram'),
              p_payload->>'owner_external_id', l.user_id)
      ON CONFLICT DO NOTHING;
    END IF;
    PERFORM public._chat_app_event(c, l.user_id, 'added');
    RETURN jsonb_build_object('knocked', true, 'conversation_id', c.id);

  ELSIF p_action = 'thread' THEN
    INSERT INTO public.chat_app_threads (conversation_id, external_thread_key)
    VALUES ((p_payload->>'conversation_id')::uuid, p_payload->>'external_thread_key')
    ON CONFLICT (conversation_id, external_thread_key) DO UPDATE SET last_activity_at = clock_timestamp()
    RETURNING * INTO t;
    IF p_payload->>'agent_session_id' IS NOT NULL
       OR coalesce((p_payload->>'reset_session')::boolean, false) THEN
      -- /new, or idle too long: the next question starts a new Agent session.
      UPDATE public.chat_app_threads SET agent_session_id = p_payload->>'agent_session_id'
      WHERE id = t.id RETURNING * INTO t;
    END IF;
    RETURN to_jsonb(t) || jsonb_build_object(
      'busy', EXISTS (SELECT 1 FROM public.chat_app_replies a WHERE a.thread_id = t.id
                      AND a.state = 'active' AND a.run_id IS NOT NULL));

  ELSIF p_action = 'reply_info' THEN
    -- Who may press Stop on this reply: the asker or the chat's owner.
    SELECT * INTO r FROM public.chat_app_replies WHERE id = (p_payload->>'id')::uuid;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO c FROM public.chat_app_conversations WHERE id = r.conversation_id;
    RETURN jsonb_build_object('id', r.id, 'run_id', r.run_id, 'state', r.state,
      'conversation_id', c.id, 'owner_user_id', c.owner_user_id, 'owner_external_id', c.owner_external_id,
      'owner_name', (SELECT coalesce(nullif(p.display_name, ''), p.email) FROM public.profiles p
                     WHERE p.user_id = c.owner_user_id),
      'asker_external_id', (SELECT q.asker_external_id FROM public.chat_app_inbox q WHERE q.id = r.inbox_id),
      'asker_label', (SELECT q.asker_label FROM public.chat_app_inbox q WHERE q.id = r.inbox_id),
      'clicker_user_id', (SELECT i2.user_id FROM public.chat_app_identities i2
        WHERE i2.platform = c.platform AND i2.external_user_id = p_payload->>'clicker'
          AND i2.scope = coalesce((SELECT external_id FROM public.chat_app_connections WHERE id = c.connection_id),
                                  'telegram')));

  ELSIF p_action = 'finish_inbox' THEN
    SELECT * INTO i FROM public.chat_app_inbox WHERE id = (p_payload->>'id')::uuid FOR UPDATE;
    IF NOT FOUND OR i.fence <> (p_payload->>'fence')::bigint OR i.lease_owner IS DISTINCT FROM p_worker THEN
      RAISE EXCEPTION 'chat_app_stale_fence' USING ERRCODE = '55000';
    END IF;
    IF p_payload->>'state' NOT IN ('done','waiting','held') THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    UPDATE public.chat_app_inbox SET state = p_payload->>'state', outcome = p_payload->>'outcome',
      conversation_id = coalesce((p_payload->>'conversation_id')::uuid, conversation_id),
      thread_id = coalesce((p_payload->>'thread_id')::uuid, thread_id),
      request_id = coalesce((p_payload->>'request_id')::uuid, request_id),
      run_id = coalesce((p_payload->>'run_id')::uuid, run_id),
      held_until = CASE WHEN p_payload->>'state' = 'held' THEN clock_timestamp() + interval '10 minutes' END,
      finished_at = CASE WHEN p_payload->>'state' = 'done' THEN clock_timestamp() END,
      lease_owner = NULL, lease_expires_at = NULL
    WHERE id = i.id RETURNING * INTO i;
    IF p_payload ? 'reply' AND i.conversation_id IS NOT NULL THEN
      -- The inbox loop has already said a notice; a Run reply is followed from now
      -- on. A queued question's "next" message becomes its working message.
      INSERT INTO public.chat_app_replies (conversation_id, thread_id, inbox_id, run_id, notice_code, payload, state)
      VALUES (i.conversation_id, i.thread_id, i.id, (p_payload->'reply'->>'run_id')::uuid,
              p_payload->'reply'->>'notice_code', coalesce(p_payload->'reply'->'payload', '{}'),
              CASE WHEN p_payload->'reply'->>'run_id' IS NULL THEN 'done' ELSE 'active' END)
      ON CONFLICT (inbox_id) WHERE inbox_id IS NOT NULL DO UPDATE SET
        run_id = EXCLUDED.run_id, notice_code = EXCLUDED.notice_code,
        payload = public.chat_app_replies.payload || EXCLUDED.payload,
        state = EXCLUDED.state, thread_id = EXCLUDED.thread_id,
        rendered_digest = NULL, next_attempt_at = NULL
      RETURNING * INTO r;
    END IF;
    RETURN jsonb_build_object('inbox', to_jsonb(i), 'reply', to_jsonb(r));

  ELSIF p_action = 'claim_reply' THEN
    -- A WeCom message can only go out through the socket of the lease holder.
    SELECT a.* INTO r FROM public.chat_app_replies a
    JOIN public.chat_app_conversations ac ON ac.id = a.conversation_id
    LEFT JOIN public.chat_app_connections ax ON ax.id = ac.connection_id
    WHERE a.state IN ('pending','active')
      AND (a.next_attempt_at IS NULL OR a.next_attempt_at <= clock_timestamp())
      AND (a.lease_expires_at IS NULL OR a.lease_expires_at <= clock_timestamp())
      AND (ac.platform <> 'wecom' OR (ax.lease_owner = p_worker AND ax.lease_expires_at > clock_timestamp()))
    ORDER BY a.next_attempt_at NULLS FIRST, a.created_at
    FOR UPDATE OF a SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE public.chat_app_replies SET lease_owner = p_worker, lease_expires_at = clock_timestamp() + lease,
      fence = fence + 1 WHERE id = r.id RETURNING * INTO r;
    SELECT * INTO c FROM public.chat_app_conversations WHERE id = r.conversation_id;
    RETURN jsonb_build_object('reply', to_jsonb(r),
      'conversation', to_jsonb(c) || jsonb_build_object(
        'owner_name', (SELECT coalesce(nullif(p.display_name, ''), p.email) FROM public.profiles p
                       WHERE p.user_id = c.owner_user_id),
        'workspace_name', (SELECT display_name FROM public.chat_app_connections WHERE id = c.connection_id)),
      'thread', (SELECT to_jsonb(th) FROM public.chat_app_threads th WHERE th.id = r.thread_id),
      'inbox', (SELECT to_jsonb(q) - 'payload' || jsonb_build_object('message_id', q.payload->'message_id',
                  'inline_query_id', q.payload->'inline_query_id', 'response_url', q.payload->'response_url',
                  'req_id', q.payload->'req_id')
                FROM public.chat_app_inbox q WHERE q.id = r.inbox_id));

  ELSIF p_action = 'save_reply' THEN
    SELECT * INTO r FROM public.chat_app_replies WHERE id = (p_payload->>'id')::uuid FOR UPDATE;
    IF NOT FOUND OR r.fence <> (p_payload->>'fence')::bigint OR r.lease_owner IS DISTINCT FROM p_worker THEN
      RAISE EXCEPTION 'chat_app_stale_fence' USING ERRCODE = '55000';
    END IF;
    IF p_payload ? 'state' AND p_payload->>'state' NOT IN ('pending','active','done','abandoned') THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    UPDATE public.chat_app_replies SET
      external_message_id = CASE WHEN p_payload ? 'external_message_id' THEN p_payload->>'external_message_id'
                                 ELSE external_message_id END,
      inline_message_id = coalesce(p_payload->>'inline_message_id', inline_message_id),
      stream_id = CASE WHEN p_payload ? 'stream_id' THEN p_payload->>'stream_id' ELSE stream_id END,
      stream_started_at = CASE WHEN p_payload ? 'stream_started_at'
                               THEN (p_payload->>'stream_started_at')::timestamptz ELSE stream_started_at END,
      rendered_digest = coalesce(p_payload->>'rendered_digest', rendered_digest),
      done_notice_sent = coalesce((p_payload->>'done_notice_sent')::boolean, done_notice_sent),
      units = coalesce((p_payload->>'units')::integer, units),
      attempts = coalesce((p_payload->>'attempts')::integer, attempts),
      payload = payload || coalesce(p_payload->'payload', '{}'),
      next_attempt_at = CASE WHEN p_payload ? 'next_attempt_at' THEN (p_payload->>'next_attempt_at')::timestamptz
                             ELSE next_attempt_at END,
      state = coalesce(p_payload->>'state', state),
      finished_at = CASE WHEN p_payload->>'state' IN ('done','abandoned') THEN clock_timestamp() ELSE finished_at END,
      lease_owner = CASE WHEN coalesce((p_payload->>'release')::boolean, false) THEN NULL ELSE lease_owner END,
      lease_expires_at = CASE WHEN coalesce((p_payload->>'release')::boolean, false) THEN NULL
                              ELSE clock_timestamp() + lease END
    WHERE id = r.id RETURNING * INTO r;
    IF r.thread_id IS NOT NULL THEN
      UPDATE public.chat_app_threads SET last_activity_at = clock_timestamp() WHERE id = r.thread_id;
    END IF;
    RETURN to_jsonb(r);

  ELSIF p_action = 'release' THEN
    IF p_payload->>'kind' = 'inbox' THEN
      UPDATE public.chat_app_inbox SET lease_owner = NULL, lease_expires_at = NULL
      WHERE id = (p_payload->>'id')::uuid AND lease_owner = p_worker AND fence = (p_payload->>'fence')::bigint;
    ELSIF p_payload->>'kind' = 'reply' THEN
      UPDATE public.chat_app_replies SET lease_owner = NULL, lease_expires_at = NULL
      WHERE id = (p_payload->>'id')::uuid AND lease_owner = p_worker AND fence = (p_payload->>'fence')::bigint;
    ELSIF p_payload->>'kind' = 'connection' THEN
      UPDATE public.chat_app_connections SET lease_owner = NULL, lease_expires_at = NULL
      WHERE id = (p_payload->>'id')::uuid AND lease_owner = p_worker AND fence = (p_payload->>'fence')::bigint;
    ELSE
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object('released', FOUND);

  ELSIF p_action = 'claim_connection' THEN
    -- One WeCom bot has one long connection: whoever holds the lease.
    SELECT * INTO x FROM public.chat_app_connections k
    WHERE k.platform = 'wecom' AND k.status IN ('pending','active') AND k.secret IS NOT NULL
      AND (k.lease_expires_at IS NULL OR k.lease_expires_at <= clock_timestamp())
    ORDER BY k.updated_at FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE public.chat_app_connections SET lease_owner = p_worker, lease_expires_at = clock_timestamp() + lease,
      fence = fence + 1 WHERE id = x.id RETURNING * INTO x;
    RETURN jsonb_build_object('id', x.id, 'external_id', x.external_id, 'secret', x.secret,
      'status', x.status, 'fence', x.fence);

  ELSIF p_action IN ('heartbeat','mark_connection') THEN
    SELECT * INTO x FROM public.chat_app_connections WHERE id = (p_payload->>'id')::uuid FOR UPDATE;
    IF NOT FOUND OR x.fence <> (p_payload->>'fence')::bigint OR x.lease_owner IS DISTINCT FROM p_worker THEN
      RAISE EXCEPTION 'chat_app_stale_fence' USING ERRCODE = '55000';
    END IF;
    IF p_action = 'heartbeat' THEN
      UPDATE public.chat_app_connections SET lease_expires_at = clock_timestamp() + lease WHERE id = x.id;
      RETURN jsonb_build_object('id', x.id, 'status', x.status);
    END IF;
    IF p_payload->>'status' NOT IN ('active','failed','displaced') THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    UPDATE public.chat_app_connections SET status = p_payload->>'status', updated_at = clock_timestamp(),
      lease_owner = CASE WHEN p_payload->>'status' = 'active' THEN lease_owner END,
      lease_expires_at = CASE WHEN p_payload->>'status' = 'active' THEN lease_expires_at END
    WHERE id = x.id RETURNING * INTO x;
    RETURN jsonb_build_object('id', x.id, 'status', x.status);

  ELSIF p_action = 'connection' THEN
    -- Resolve a platform account to our row (Slack team, WeCom bot); secret
    -- only for the process that is about to call the platform.
    SELECT * INTO x FROM public.chat_app_connections
    WHERE (p_payload ? 'id' AND id = (p_payload->>'id')::uuid)
       OR (platform = platform_name AND external_id = p_payload->>'external_id');
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('id', x.id, 'platform', x.platform, 'external_id', x.external_id,
      'org_id', x.org_id, 'display_name', x.display_name, 'status', x.status,
      'secret', CASE WHEN coalesce((p_payload->>'with_secret')::boolean, false) THEN x.secret END);

  ELSIF p_action = 'install_slack' THEN
    SELECT * INTO l FROM public.chat_app_link_codes
    WHERE code_hash = p_payload->>'code_hash' AND purpose = 'slack_install' FOR UPDATE;
    IF NOT FOUND OR l.consumed_at IS NOT NULL OR l.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'chat_app_link_expired' USING ERRCODE = '55000';
    END IF;
    SELECT * INTO x FROM public.chat_app_connections
    WHERE platform = 'slack' AND external_id = p_payload->>'external_id' FOR UPDATE;
    IF FOUND AND x.status <> 'revoked'
       AND x.org_id <> (SELECT org_id FROM public.projects WHERE id = l.project_id) THEN
      RAISE EXCEPTION 'chat_app_connection_taken' USING ERRCODE = '55000';
    END IF;
    INSERT INTO public.chat_app_connections (org_id, platform, external_id, display_name, secret, status, created_by)
    VALUES ((SELECT org_id FROM public.projects WHERE id = l.project_id), 'slack', p_payload->>'external_id',
            left(coalesce(nullif(p_payload->>'display_name', ''), 'Slack'), 200), p_payload->>'secret', 'active', l.user_id)
    ON CONFLICT (platform, external_id) DO UPDATE SET org_id = EXCLUDED.org_id, secret = EXCLUDED.secret,
      display_name = EXCLUDED.display_name, status = 'active', updated_at = clock_timestamp()
    RETURNING * INTO x;
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp(), connection_id = x.id WHERE id = l.id;
    INSERT INTO public.chat_app_identities (platform, scope, external_user_id, user_id)
    VALUES ('slack', x.external_id, p_payload->>'installer_external_id', l.user_id)
    ON CONFLICT (platform, scope, external_user_id) DO UPDATE SET user_id = EXCLUDED.user_id;
    RETURN jsonb_build_object('id', x.id, 'display_name', x.display_name, 'project_id', l.project_id);

  ELSIF p_action = 'revoke_connection' THEN
    UPDATE public.chat_app_connections SET status = 'revoked', secret = NULL, lease_owner = NULL,
      lease_expires_at = NULL, updated_at = clock_timestamp()
    WHERE platform = platform_name AND external_id = p_payload->>'external_id'
    RETURNING * INTO x;
    IF NOT FOUND THEN RETURN jsonb_build_object('revoked', false); END IF;
    FOR c IN UPDATE public.chat_app_conversations SET stopped_reason = 'removed_there', updated_at = clock_timestamp()
             WHERE connection_id = x.id AND state = 'active' AND stopped_reason IS DISTINCT FROM 'removed_there'
             RETURNING * LOOP
      PERFORM public._chat_app_event(c, NULL, 'removed_there');
    END LOOP;
    UPDATE public.chat_app_conversations SET state = 'removed', updated_at = clock_timestamp()
    WHERE connection_id = x.id AND state = 'asking';
    UPDATE public.chat_app_replies SET state = 'abandoned', finished_at = clock_timestamp()
    WHERE state IN ('pending','active') AND conversation_id IN
      (SELECT id FROM public.chat_app_conversations WHERE connection_id = x.id);
    RETURN jsonb_build_object('revoked', true, 'id', x.id);

  ELSIF p_action = 'removed_there' THEN
    SELECT * INTO c FROM public.chat_app_conversations
    WHERE platform = platform_name AND connection_id IS NOT DISTINCT FROM connection
      AND external_chat_id = p_payload->>'external_chat_id' AND state IN ('asking','active') FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('marked', false); END IF;
    IF c.state = 'asking' THEN
      UPDATE public.chat_app_conversations SET state = 'removed', updated_at = clock_timestamp() WHERE id = c.id;
    ELSE
      UPDATE public.chat_app_conversations SET stopped_reason = 'removed_there', updated_at = clock_timestamp()
      WHERE id = c.id RETURNING * INTO c;
      PERFORM public._chat_app_event(c, NULL, 'removed_there');
    END IF;
    UPDATE public.chat_app_replies SET state = 'abandoned', finished_at = clock_timestamp()
    WHERE conversation_id = c.id AND state = 'pending' AND run_id IS NULL;
    RETURN jsonb_build_object('marked', true, 'conversation_id', c.id);

  ELSIF p_action = 'identity_code' THEN
    INSERT INTO public.chat_app_link_codes (purpose, code_hash, platform, scope, external_user_id, expires_at)
    VALUES ('identity', p_payload->>'code_hash', platform_name, p_payload->>'scope',
            p_payload->>'external_user_id', clock_timestamp() + interval '10 minutes')
    RETURNING * INTO l;
    RETURN jsonb_build_object('id', l.id, 'expires_at', l.expires_at);

  ELSIF p_action = 'scrub' THEN
    -- Question text is kept in Agent history; the inbox copy goes after 7 days.
    UPDATE public.chat_app_inbox SET payload = payload - 'text'
    WHERE state = 'done' AND finished_at < clock_timestamp() - interval '7 days' AND payload ? 'text';
    RETURN jsonb_build_object('scrubbed', true);
  END IF;
  RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
END $$;

REVOKE ALL ON FUNCTION public.chat_app_command(text, uuid, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.chat_app_gateway_command(text, text, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.chat_app_command(text, uuid, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.chat_app_gateway_command(text, text, jsonb) TO service_role;
DO $$ DECLARE f text; BEGIN
  FOREACH f IN ARRAY ARRAY['_chat_app_role(text,uuid)','_chat_app_can_run(text,uuid)',
    '_chat_app_used_today(uuid)','_chat_app_event(public.chat_app_conversations,uuid,text,jsonb)',
    '_chat_app_conversation_json(public.chat_app_conversations)',
    '_chat_app_connection_json(public.chat_app_connections,uuid)',
    '_chat_app_check_owner(public.chat_app_conversations)','_chat_app_link_json(public.chat_app_link_codes)'] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, anon, authenticated, service_role', f);
  END LOOP;
END $$;

-- Project activity: management actions on chats, never message text.
DO $$ DECLARE definition text; BEGIN
  definition := rtrim(btrim(pg_get_viewdef('public.context_activity_items'::regclass, true)), ';');
  IF position('chat_app' IN definition) > 0 THEN
    RAISE EXCEPTION 'CHAT_APP_ACTIVITY_ALREADY_PRESENT';
  END IF;
  EXECUTE 'CREATE OR REPLACE VIEW public.context_activity_items WITH (security_invoker=true) AS '
    || definition || $view$
UNION ALL
 SELECT e.id::text AS id,
    'chat_app'::text AS kind,
    e.project_id,
    e.actor_user_id AS created_by,
    e.label,
    e.action AS status,
    e.platform AS phase,
    100 AS progress,
    NULL::text AS message,
    NULL::text AS error_message,
    NULL::text AS result_path,
    NULL::text AS result_commit_id,
    e.created_at,
    e.created_at AS completed_at
   FROM public.chat_app_events e$view$;
END $$;
REVOKE ALL ON public.context_activity_items FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.context_activity_items TO service_role;

COMMIT;
