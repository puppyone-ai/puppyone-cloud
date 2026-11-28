-- Additive: the project's Cloud Agent in Telegram, Slack and WeCom chats.
-- Only adds objects; no existing table, view, function or row is changed.
-- 1. Six service-only tables (RLS on, no client grants).
-- 2. chat_app_command: people (start a link, connect, allow, change, take over,
--    remove). Every action re-checks the project role.
-- 3. chat_app_gateway_command: processes (intake, leases with fences, replies,
--    WeCom connection ownership). Rows are claimed with SKIP LOCKED.
-- Whoever brings a chat in proves it with a one-time code (Telegram link, WeCom
-- code) or, on Slack, is the person who installed PuppyOne there; a manager
-- still lets each chat in. No chat account is mapped to a PuppyOne account.
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
    -- Slack: the account that installed it (created_by in Slack). Only its
    -- channel invites are matched to a wait in PuppyOne.
    installer_external_id text
        CHECK (installer_external_id IS NULL OR length(installer_external_id) BETWEEN 1 AND 200),
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
    -- The owner's account in the chat app: who brought the chat in with the
    -- code. Unknown after a take over until the new owner brings it in again.
    owner_external_id text,
    who_can_ask text NOT NULL DEFAULT 'owner' CHECK (who_can_ask IN ('owner','anyone')),
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
CREATE INDEX chat_app_conversations_connection ON public.chat_app_conversations (connection_id)
    WHERE connection_id IS NOT NULL;

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
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','waiting','done')),
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
    WHERE state IN ('pending','waiting');
CREATE INDEX chat_app_inbox_thread ON public.chat_app_inbox (thread_id, created_at)
    WHERE state IN ('pending','waiting');
-- Read for every question (hourly guards) and every chat listed (no-hours banner).
CREATE INDEX chat_app_inbox_conversation ON public.chat_app_inbox (conversation_id, created_at);
-- Slack invites that named no inviter, matched by the next mention.
CREATE INDEX chat_app_inbox_joined ON public.chat_app_inbox (connection_id, external_chat_id, created_at)
    WHERE kind = 'joined';
-- Retention: done rows go after 30 days.
CREATE INDEX chat_app_inbox_finished ON public.chat_app_inbox (finished_at) WHERE state = 'done';

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
CREATE INDEX chat_app_replies_thread ON public.chat_app_replies (thread_id)
    WHERE state IN ('pending','active');
-- A reply to one of our messages continues that message's thread.
CREATE INDEX chat_app_replies_message ON public.chat_app_replies (external_message_id)
    WHERE external_message_id IS NOT NULL;
-- Retention: finished messages go after 30 days.
CREATE INDEX chat_app_replies_finished ON public.chat_app_replies (finished_at)
    WHERE state IN ('done','abandoned');

CREATE TABLE public.chat_app_link_codes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    purpose text NOT NULL CHECK (purpose IN ('knock','slack_install','slack_wait')),
    code_hash text NOT NULL UNIQUE CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    platform text NOT NULL CHECK (platform IN ('telegram','slack','wecom')),
    project_id text NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    connection_id uuid REFERENCES public.chat_app_connections(id) ON DELETE CASCADE,
    conversation_id uuid REFERENCES public.chat_app_conversations(id) ON DELETE SET NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX chat_app_link_codes_open ON public.chat_app_link_codes (project_id, user_id, platform, purpose)
    WHERE consumed_at IS NULL;
CREATE INDEX chat_app_link_codes_slack_wait ON public.chat_app_link_codes (connection_id, created_at)
    WHERE purpose = 'slack_wait' AND consumed_at IS NULL;
CREATE INDEX chat_app_link_codes_expires ON public.chat_app_link_codes (expires_at);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['chat_app_connections','chat_app_conversations','chat_app_threads',
    'chat_app_inbox','chat_app_replies','chat_app_link_codes'] LOOP
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

-- What a client may see of a chat. Never a secret.
CREATE FUNCTION public._chat_app_conversation_json(c public.chat_app_conversations)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT jsonb_build_object(
    'id', c.id, 'project_id', c.project_id, 'platform', c.platform, 'connection_id', c.connection_id,
    'chat_kind', c.chat_kind, 'display_name', c.display_name, 'knock_hint', c.knock_hint,
    'state', c.state, 'enabled', c.enabled, 'owner_user_id', c.owner_user_id,
    'owner_name', (SELECT coalesce(nullif(p.display_name, ''), p.email) FROM public.profiles p
                   WHERE p.user_id = c.owner_user_id),
    'owner_known', c.owner_external_id IS NOT NULL,
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
                          ORDER BY q.created_at DESC LIMIT 1), false),
    'created_at', c.created_at)
$$;

-- linked_me: this person installed it (Slack: they can add channels from it).
CREATE FUNCTION public._chat_app_connection_json(x public.chat_app_connections, p_user uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT jsonb_build_object('id', x.id, 'platform', x.platform, 'external_id', x.external_id,
    'display_name', x.display_name, 'status', x.status, 'created_at', x.created_at,
    'linked_me', x.created_by IS NOT DISTINCT FROM p_user
                 AND (x.platform <> 'slack' OR x.installer_external_id IS NOT NULL))
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

-- A connection's chats stop: the app removed us, or the bot moved to another space.
CREATE FUNCTION public._chat_app_stop_connection(p_connection uuid, p_remove boolean)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  UPDATE public.chat_app_replies SET state = 'abandoned', finished_at = clock_timestamp()
  WHERE state IN ('pending','active') AND conversation_id IN
    (SELECT id FROM public.chat_app_conversations WHERE connection_id = p_connection);
  UPDATE public.chat_app_conversations SET state = 'removed', updated_at = clock_timestamp()
  WHERE connection_id = p_connection AND (state = 'asking' OR (p_remove AND state = 'active'));
  UPDATE public.chat_app_conversations SET stopped_reason = 'removed_there', updated_at = clock_timestamp()
  WHERE connection_id = p_connection AND state = 'active' AND stopped_reason IS DISTINCT FROM 'removed_there';
$$;

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
      -- Slack invites are matched to the installer, so only they can wait in it.
      IF platform_name = 'slack' AND (x.created_by IS DISTINCT FROM p_user OR x.installer_external_id IS NULL) THEN
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
    -- A bot belongs to the space it works for, or that is checking it now. One
    -- that failed, was displaced or was revoked can be connected elsewhere.
    IF FOUND AND x.org_id <> organization AND (x.status = 'active'
       OR (x.status = 'pending' AND x.updated_at > clock_timestamp() - interval '5 minutes')) THEN
      RAISE EXCEPTION 'chat_app_connection_taken' USING ERRCODE = '55000';
    END IF;
    IF FOUND AND x.org_id <> organization THEN
      -- The other space's chats on this bot end: its messages now come here.
      PERFORM public._chat_app_stop_connection(x.id, true);
    END IF;
    INSERT INTO public.chat_app_connections (org_id, platform, external_id, display_name, secret, status, created_by)
    VALUES (organization, 'wecom', p_payload->>'external_id', coalesce(nullif(p_payload->>'display_name', ''), 'WeCom bot'),
            p_payload->>'secret', 'pending', p_user)
    ON CONFLICT (platform, external_id) DO UPDATE SET org_id = EXCLUDED.org_id, secret = EXCLUDED.secret,
      display_name = EXCLUDED.display_name, status = 'pending', created_by = EXCLUDED.created_by,
      lease_owner = NULL, lease_expires_at = NULL, fence = public.chat_app_connections.fence + 1,
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
      IF p_payload->>'who_can_ask' NOT IN ('owner','anyone') THEN
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
  ELSIF p_action = 'take_over' THEN
    IF c.state <> 'active' OR c.stopped_reason IS DISTINCT FROM 'owner_left' THEN
      RAISE EXCEPTION 'chat_app_conflict' USING ERRCODE = '55000';
    END IF;
    -- The old owner's chat account must not keep asking as the new owner. The
    -- new owner's own account is known only on Slack, when they installed it.
    UPDATE public.chat_app_conversations SET owner_user_id = p_user, stopped_reason = NULL,
      owner_external_id = (SELECT k.installer_external_id FROM public.chat_app_connections k
        WHERE k.id = c.connection_id AND k.platform = 'slack' AND k.created_by = p_user),
      updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
  ELSIF p_action = 'remove' THEN
    UPDATE public.chat_app_conversations SET state = 'removed', updated_at = clock_timestamp()
    WHERE id = c.id RETURNING * INTO c;
    UPDATE public.chat_app_inbox SET state = 'done', outcome = 'removed', finished_at = clock_timestamp(),
      lease_owner = NULL, lease_expires_at = NULL
    WHERE conversation_id = c.id AND state IN ('pending','waiting');
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
      -- Slack may not say who invited it; the installer's next mention within 10 minutes decides.
      'join_without_inviter', EXISTS (SELECT 1 FROM public.chat_app_inbox j
        WHERE j.connection_id IS NOT DISTINCT FROM connection AND j.kind = 'joined'
          AND j.external_chat_id = p_payload->>'external_chat_id' AND j.platform = platform_name
          AND j.outcome = 'inviter_missing' AND j.created_at > clock_timestamp() - interval '10 minutes'),
      'used_today_units', CASE WHEN c.id IS NULL THEN 0 ELSE public._chat_app_used_today(c.id) END,
      -- Paused / limit / "only X can ask" are said to one person once an hour.
      'notified_last_hour', coalesce((SELECT jsonb_agg(DISTINCT q.outcome) FROM public.chat_app_inbox q
        WHERE q.conversation_id = c.id AND q.created_at > clock_timestamp() - interval '1 hour'
          AND q.asker_external_id = p_payload->>'asker_external_id'
          AND q.outcome IN ('paused','limit','only_owner')), '[]'),
      'asked_last_hour', (SELECT count(*) FROM public.chat_app_inbox q WHERE q.conversation_id = c.id
                          AND q.kind = 'question' AND q.created_at > clock_timestamp() - interval '1 hour'),
      'thread', CASE WHEN t.id IS NULL THEN NULL ELSE to_jsonb(t) || jsonb_build_object(
        'busy', EXISTS (SELECT 1 FROM public.chat_app_replies a WHERE a.thread_id = t.id
                        AND a.state = 'active' AND a.run_id IS NOT NULL),
        'waiting', (SELECT count(*) FROM public.chat_app_inbox w WHERE w.thread_id = t.id
                    AND w.state = 'waiting' AND w.id IS DISTINCT FROM (p_payload->>'inbox_id')::uuid)) END);

  ELSIF p_action = 'knock' THEN
    IF platform_name = 'slack' THEN
      -- A channel invite knocks only when the installer made it while their
      -- wait in PuppyOne is open; anyone else's invite is not matched.
      SELECT * INTO x FROM public.chat_app_connections WHERE id = connection;
      IF NOT FOUND OR x.installer_external_id IS NULL
         OR x.installer_external_id IS DISTINCT FROM p_payload->>'owner_external_id' THEN
        RETURN jsonb_build_object('knocked', false, 'reason', 'not_installer');
      END IF;
      SELECT * INTO l FROM public.chat_app_link_codes k
      WHERE k.purpose = 'slack_wait' AND k.connection_id = connection AND k.consumed_at IS NULL
        AND k.expires_at > clock_timestamp() AND k.user_id = x.created_by
      ORDER BY k.created_at DESC LIMIT 1 FOR UPDATE;
      IF NOT FOUND THEN
        RETURN jsonb_build_object('knocked', false, 'reason', 'no_wait');
      END IF;
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
    -- Whoever brought the chat in with the code owns it there. Nothing else
    -- is recorded about them until a manager lets the chat in.
    INSERT INTO public.chat_app_conversations (project_id, org_id, platform, connection_id, external_chat_id,
      chat_kind, display_name, knock_hint, owner_user_id, owner_external_id, link_id)
    VALUES (l.project_id, (SELECT org_id FROM public.projects WHERE id = l.project_id), platform_name, connection,
      p_payload->>'external_chat_id', p_payload->>'chat_kind',
      left(coalesce(nullif(btrim(p_payload->>'display_name'), ''), 'Chat'), 64),
      p_payload->>'knock_hint', l.user_id, p_payload->>'owner_external_id', l.id)
    RETURNING * INTO c;
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp(), conversation_id = c.id WHERE id = l.id;
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
      'asker_label', (SELECT q.asker_label FROM public.chat_app_inbox q WHERE q.id = r.inbox_id));

  ELSIF p_action = 'finish_inbox' THEN
    SELECT * INTO i FROM public.chat_app_inbox WHERE id = (p_payload->>'id')::uuid FOR UPDATE;
    IF NOT FOUND OR i.fence <> (p_payload->>'fence')::bigint OR i.lease_owner IS DISTINCT FROM p_worker THEN
      RAISE EXCEPTION 'chat_app_stale_fence' USING ERRCODE = '55000';
    END IF;
    IF p_payload->>'state' NOT IN ('done','waiting') THEN
      RAISE EXCEPTION 'chat_app_invalid_request' USING ERRCODE = '22023';
    END IF;
    UPDATE public.chat_app_inbox SET state = p_payload->>'state', outcome = p_payload->>'outcome',
      conversation_id = coalesce((p_payload->>'conversation_id')::uuid, conversation_id),
      thread_id = coalesce((p_payload->>'thread_id')::uuid, thread_id),
      request_id = coalesce((p_payload->>'request_id')::uuid, request_id),
      run_id = coalesce((p_payload->>'run_id')::uuid, run_id),
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
                  'guest_query_id', q.payload->'guest_query_id', 'response_url', q.payload->'response_url',
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
    -- Whoever installs (or installs again) becomes the person whose channel
    -- invites are matched: Slack's OAuth names their account.
    INSERT INTO public.chat_app_connections (org_id, platform, external_id, display_name, secret, status,
      created_by, installer_external_id)
    VALUES ((SELECT org_id FROM public.projects WHERE id = l.project_id), 'slack', p_payload->>'external_id',
            left(coalesce(nullif(p_payload->>'display_name', ''), 'Slack'), 200), p_payload->>'secret', 'active',
            l.user_id, nullif(p_payload->>'installer_external_id', ''))
    ON CONFLICT (platform, external_id) DO UPDATE SET org_id = EXCLUDED.org_id, secret = EXCLUDED.secret,
      display_name = EXCLUDED.display_name, status = 'active', created_by = EXCLUDED.created_by,
      installer_external_id = EXCLUDED.installer_external_id, updated_at = clock_timestamp()
    RETURNING * INTO x;
    UPDATE public.chat_app_link_codes SET consumed_at = clock_timestamp(), connection_id = x.id WHERE id = l.id;
    RETURN jsonb_build_object('id', x.id, 'display_name', x.display_name, 'project_id', l.project_id);

  ELSIF p_action = 'revoke_connection' THEN
    UPDATE public.chat_app_connections SET status = 'revoked', secret = NULL, lease_owner = NULL,
      lease_expires_at = NULL, updated_at = clock_timestamp()
    WHERE platform = platform_name AND external_id = p_payload->>'external_id'
    RETURNING * INTO x;
    IF NOT FOUND THEN RETURN jsonb_build_object('revoked', false); END IF;
    PERFORM public._chat_app_stop_connection(x.id, false);
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
      WHERE id = c.id;
    END IF;
    UPDATE public.chat_app_replies SET state = 'abandoned', finished_at = clock_timestamp()
    WHERE conversation_id = c.id AND state = 'pending' AND run_id IS NULL;
    RETURN jsonb_build_object('marked', true, 'conversation_id', c.id);

  ELSIF p_action = 'scrub' THEN
    -- Question text is kept in Agent history; the inbox copy goes after 7 days.
    UPDATE public.chat_app_inbox SET payload = payload - 'text'
    WHERE state = 'done' AND finished_at < clock_timestamp() - interval '7 days' AND payload ? 'text';
    -- Finished rows go after 30 days (a daily limit reads only today's replies),
    -- spent codes after a day.
    DELETE FROM public.chat_app_replies
    WHERE state IN ('done','abandoned') AND finished_at < clock_timestamp() - interval '30 days';
    DELETE FROM public.chat_app_inbox
    WHERE state = 'done' AND finished_at < clock_timestamp() - interval '30 days';
    DELETE FROM public.chat_app_link_codes WHERE expires_at < clock_timestamp() - interval '1 day';
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
    '_chat_app_used_today(uuid)',
    '_chat_app_conversation_json(public.chat_app_conversations)',
    '_chat_app_connection_json(public.chat_app_connections,uuid)',
    '_chat_app_check_owner(public.chat_app_conversations)',
    '_chat_app_stop_connection(uuid,boolean)',
    '_chat_app_link_json(public.chat_app_link_codes)'] LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, anon, authenticated, service_role', f);
  END LOOP;
END $$;

COMMIT;
