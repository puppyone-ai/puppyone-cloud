-- Phase 3: run a terminal, Git or an Agent in a registered folder on the owner's
-- own computer. Additive; no data is rewritten. Every grant here is also gated
-- on the host itself, which keeps execution off per folder until its user allows it.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

-- Execution capabilities exist only on a folder that may be edited.
ALTER TABLE public.host_folders DROP CONSTRAINT host_folders_capabilities_check;
ALTER TABLE public.host_folders ADD CONSTRAINT host_folders_capabilities_check CHECK (
  capabilities <@ ARRAY['folder.read','folder.write','folder.shell','folder.git','folder.agent']::text[]
  AND 'folder.read'=ANY(capabilities) AND cardinality(capabilities) BETWEEN 1 AND 5
  AND array_position(capabilities,NULL) IS NULL
  AND (capabilities <@ ARRAY['folder.read','folder.write']::text[] OR 'folder.write'=ANY(capabilities)));

ALTER TABLE public.host_file_events DROP CONSTRAINT host_file_events_operation_check;
ALTER TABLE public.host_file_events ADD CONSTRAINT host_file_events_operation_check CHECK (
  operation IN ('list','read','write','create','move','remove','mkdir','watch','shell','git','agent'));

-- One row per running terminal or Agent, fenced by the device connection that
-- serves it. A reconnect, a revoked device or a withdrawn capability stops renewal.
CREATE TABLE public.host_exec_sessions (
 id uuid PRIMARY KEY,
 device_id uuid NOT NULL REFERENCES public.host_devices(id) ON DELETE CASCADE,
 folder_id uuid NOT NULL REFERENCES public.host_folders(id) ON DELETE CASCADE,
 actor_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK (kind IN ('shell','agent')),
 epoch uuid NOT NULL,
 gateway_instance text NOT NULL CHECK (length(btrim(gateway_instance)) BETWEEN 1 AND 120),
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','closed')),
 lease_until timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 closed_at timestamptz,
 exit_status integer,
 CHECK ((state='closed')=(closed_at IS NOT NULL))
);
CREATE INDEX host_exec_sessions_device ON public.host_exec_sessions(device_id,state,lease_until);
ALTER TABLE public.host_exec_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.host_exec_sessions FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.host_capability_for(p_operation text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT CASE
   WHEN p_operation IN ('list','read','watch') THEN 'folder.read'
   WHEN p_operation IN ('write','create','move','remove','mkdir') THEN 'folder.write'
   WHEN p_operation='shell' THEN 'folder.shell'
   WHEN p_operation='git' THEN 'folder.git'
   WHEN p_operation='agent' THEN 'folder.agent'
 END
$$;
REVOKE ALL ON FUNCTION public.host_capability_for(text) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION public.host_folder_admit(p_actor uuid,p_folder uuid,p_operation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE admitted jsonb; needed text:=public.host_capability_for(p_operation);
BEGIN
 IF needed IS NULL THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('device_id',d.id,'folder_id',f.id,'epoch',c.epoch,
   'gateway_instance',c.gateway_instance,'device_revision',d.revision,
   'folder_revision',f.revision,'lease_until',c.lease_until,'protocol',c.protocol,
   'online',coalesce(c.lease_until>clock_timestamp(),false)) INTO admitted
 FROM public.host_folders f JOIN public.host_devices d ON d.id=f.device_id
 LEFT JOIN public.host_connections c ON c.device_id=d.id
 WHERE f.id=p_folder AND d.owner_user_id=p_actor AND d.state='active' AND f.state='active'
   AND needed=ANY(f.capabilities);
 IF admitted IS NULL THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 IF NOT (admitted->>'online')::boolean THEN RAISE EXCEPTION 'host_offline' USING ERRCODE='55000'; END IF;
 RETURN admitted - 'online';
END $$;

CREATE OR REPLACE FUNCTION public.host_file_event_record(p_actor uuid,p_device uuid,p_folder uuid,p_operation text,p_outcome text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_operation IS NULL OR p_outcome IS NULL OR
   p_operation NOT IN ('list','read','write','mkdir','move','remove','watch','shell','git','agent') OR
   p_outcome NOT IN ('intended','succeeded','failed','unknown') THEN
  RAISE EXCEPTION 'host_invalid_audit' USING ERRCODE='22023'; END IF;
 INSERT INTO public.host_file_events(device_id,folder_id,actor_user_id,operation,outcome)
 SELECT d.id,f.id,p_actor,p_operation,p_outcome FROM public.host_devices d
 JOIN public.host_folders f ON f.device_id=d.id
 WHERE d.id=p_device AND d.owner_user_id=p_actor AND f.id=p_folder;
 IF NOT FOUND THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 RETURN true;
END $$;

-- Registration still grants files only; running things is granted later, from
-- the host, through host_folder_update.
CREATE OR REPLACE FUNCTION public.host_folder_update(p_owner uuid,p_device uuid,p_folder uuid,p_name text,
  p_capabilities text[],p_expected_revision bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE updated public.host_folders;
BEGIN
 IF p_owner IS NULL OR p_folder IS NULL OR p_expected_revision IS NULL OR
   p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 1 AND 120 OR p_capabilities IS NULL OR
   NOT (p_capabilities <@ ARRAY['folder.read','folder.write','folder.shell','folder.git','folder.agent']::text[]) OR
   NOT ('folder.read'=ANY(p_capabilities)) OR cardinality(p_capabilities) NOT BETWEEN 1 AND 5 OR
   array_position(p_capabilities,NULL) IS NOT NULL OR
   NOT (p_capabilities <@ ARRAY['folder.read','folder.write']::text[] OR 'folder.write'=ANY(p_capabilities)) THEN
   RAISE EXCEPTION 'host_invalid_folder' USING ERRCODE='22023';
 END IF;
 PERFORM 1 FROM public.host_devices WHERE id=p_device AND owner_user_id=p_owner AND state='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO updated FROM public.host_folders WHERE id=p_folder AND device_id=p_device AND state='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 IF updated.display_name=btrim(p_name) AND updated.capabilities=p_capabilities THEN
   RETURN jsonb_build_object('id',updated.id,'revision',updated.revision);
 END IF;
 IF updated.revision<>p_expected_revision THEN
   RAISE EXCEPTION 'host_folder_changed' USING ERRCODE='PT409';
 END IF;
 UPDATE public.host_folders SET display_name=btrim(p_name),capabilities=p_capabilities,
   revision=revision+1,updated_at=clock_timestamp()
 WHERE id=p_folder RETURNING * INTO updated;
 -- A withdrawn capability ends its running sessions at the next renewal.
 RETURN jsonb_build_object('id',updated.id,'revision',updated.revision);
END $$;

CREATE FUNCTION public.host_exec_open(p_actor uuid,p_folder uuid,p_session uuid,p_kind text,
  p_gateway text,p_lease_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE admitted jsonb; device uuid;
BEGIN
 IF p_session IS NULL OR p_kind IS NULL OR p_kind NOT IN ('shell','agent') OR p_gateway IS NULL OR
   length(btrim(p_gateway)) NOT BETWEEN 1 AND 120 OR p_lease_seconds NOT BETWEEN 10 AND 120 THEN
   RAISE EXCEPTION 'host_invalid_exec' USING ERRCODE='22023';
 END IF;
 admitted:=public.host_folder_admit(p_actor,p_folder,p_kind);
 IF admitted->>'gateway_instance'<>p_gateway THEN
   RAISE EXCEPTION 'host_offline' USING ERRCODE='55000';
 END IF;
 device:=(admitted->>'device_id')::uuid;
 PERFORM 1 FROM public.host_devices WHERE id=device FOR UPDATE;
 IF (SELECT count(*) FROM public.host_exec_sessions WHERE device_id=device AND state='active'
     AND lease_until>clock_timestamp())>=8 THEN
   RAISE EXCEPTION 'host_exec_limit' USING ERRCODE='22023';
 END IF;
 INSERT INTO public.host_exec_sessions(id,device_id,folder_id,actor_user_id,kind,epoch,gateway_instance,lease_until)
 VALUES(p_session,device,p_folder,p_actor,p_kind,(admitted->>'epoch')::uuid,p_gateway,
   clock_timestamp()+make_interval(secs=>p_lease_seconds));
 RETURN jsonb_build_object('device_id',device,'epoch',admitted->>'epoch');
END $$;

-- Renewal re-checks everything: the device, the folder, the capability, the
-- owner and the very connection epoch the session started on.
CREATE FUNCTION public.host_exec_renew(p_session uuid,p_gateway text,p_lease_seconds integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_lease_seconds NOT BETWEEN 10 AND 120 THEN RETURN false; END IF;
 UPDATE public.host_exec_sessions s SET lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds)
 FROM public.host_folders f, public.host_devices d, public.host_connections c
 WHERE s.id=p_session AND s.state='active' AND s.lease_until>clock_timestamp() AND s.gateway_instance=p_gateway
   AND f.id=s.folder_id AND f.state='active' AND public.host_capability_for(s.kind)=ANY(f.capabilities)
   AND d.id=s.device_id AND d.state='active' AND d.owner_user_id=s.actor_user_id
   AND c.device_id=s.device_id AND c.epoch=s.epoch AND c.gateway_instance=p_gateway
   AND c.lease_until>clock_timestamp();
 RETURN FOUND;
END $$;

CREATE FUNCTION public.host_exec_close(p_session uuid,p_exit_status integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 UPDATE public.host_exec_sessions SET state='closed',closed_at=clock_timestamp(),exit_status=p_exit_status
 WHERE id=p_session AND state='active';
 RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION public.host_exec_open(uuid,uuid,uuid,text,text,integer),
 public.host_exec_renew(uuid,text,integer),public.host_exec_close(uuid,integer)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_exec_open(uuid,uuid,uuid,text,text,integer),
 public.host_exec_renew(uuid,text,integer),public.host_exec_close(uuid,integer)
 TO service_role;
COMMIT;
