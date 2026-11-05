-- Additive control-plane inventory only. No network or file access is enabled by this schema.
-- Devices are not Access registrations or Cloud workbenches; no local path is stored here.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE TABLE public.host_devices (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 public_key bytea NOT NULL UNIQUE CHECK (octet_length(public_key) = 32),
 display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
 platform text NOT NULL CHECK (platform IN ('darwin','win32','linux')),
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','paused','revoked')),
 revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 last_seen_at timestamptz
);
CREATE INDEX host_devices_owner ON public.host_devices(owner_user_id,id);

CREATE TABLE public.host_folders (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 device_id uuid NOT NULL REFERENCES public.host_devices(id) ON DELETE CASCADE,
 display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
 capabilities text[] NOT NULL DEFAULT ARRAY['folder.read']::text[]
   CHECK (capabilities <@ ARRAY['folder.read','folder.write']::text[]
     AND 'folder.read'=ANY(capabilities) AND cardinality(capabilities) BETWEEN 1 AND 2
     AND array_position(capabilities,NULL) IS NULL),
 state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','paused','removed')),
 revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX host_folders_device ON public.host_folders(device_id,state,id);

-- One current slot per device. An atomic compare-and-swap will replace the epoch
-- on reconnect; a stale connection must never renew or deliver a reply for it.
CREATE TABLE public.host_connections (
 device_id uuid PRIMARY KEY REFERENCES public.host_devices(id) ON DELETE CASCADE,
 epoch uuid NOT NULL,
 gateway_instance text NOT NULL CHECK (length(btrim(gateway_instance)) BETWEEN 1 AND 120),
 lease_until timestamptz NOT NULL,
 connected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 renewed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX host_connections_lease ON public.host_connections(lease_until);

-- Metadata only. No file content or client filesystem path is admitted here.
CREATE TABLE public.host_file_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 device_id uuid NOT NULL,
 folder_id uuid NOT NULL,
 actor_user_id uuid NOT NULL,
 operation text NOT NULL CHECK (operation IN ('list','read','write','create','move','remove','mkdir','watch')),
 outcome text NOT NULL CHECK (outcome IN ('intended','succeeded','failed','unknown')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX host_file_events_device_time ON public.host_file_events(device_id,created_at DESC,id);

-- Calls must go through an authenticated API and later, atomic service-only RPCs.
-- A service_role direct-table read is deliberately not granted by this migration.
DO $$ DECLARE relation text; BEGIN
 FOREACH relation IN ARRAY ARRAY['host_devices','host_folders','host_connections','host_file_events'] LOOP
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',relation);
   EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated,service_role',relation);
 END LOOP;
END $$;
REVOKE ALL ON SEQUENCE public.host_file_events_id_seq FROM PUBLIC,anon,authenticated,service_role;

-- The gateway has already verified a device-key challenge before calling open.
-- A service-role caller is still not allowed to revive a revoked device.
CREATE FUNCTION public.host_connection_open(p_device uuid,p_epoch uuid,p_gateway text,p_lease_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE device public.host_devices; connection public.host_connections;
BEGIN
 IF p_epoch IS NULL OR p_gateway IS NULL OR length(btrim(p_gateway)) NOT BETWEEN 1 AND 120
   OR p_lease_seconds NOT BETWEEN 10 AND 120 THEN
   RAISE EXCEPTION 'host_invalid_connection' USING ERRCODE='22023';
 END IF;
 SELECT * INTO device FROM public.host_devices WHERE id=p_device FOR UPDATE;
 IF NOT FOUND OR device.state<>'active' THEN
   RAISE EXCEPTION 'host_unavailable' USING ERRCODE='42501';
 END IF;
 INSERT INTO public.host_connections(device_id,epoch,gateway_instance,lease_until)
 VALUES(p_device,p_epoch,p_gateway,clock_timestamp()+make_interval(secs=>p_lease_seconds))
 ON CONFLICT (device_id) DO UPDATE SET epoch=EXCLUDED.epoch,
   gateway_instance=EXCLUDED.gateway_instance,lease_until=EXCLUDED.lease_until,
   connected_at=clock_timestamp(),renewed_at=clock_timestamp()
 RETURNING * INTO connection;
 RETURN jsonb_build_object('epoch',connection.epoch,'lease_until',connection.lease_until);
END $$;

CREATE FUNCTION public.host_connection_renew(p_device uuid,p_epoch uuid,p_gateway text,p_lease_seconds integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_lease_seconds NOT BETWEEN 10 AND 120 THEN RETURN false; END IF;
 UPDATE public.host_connections c SET lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds),
   renewed_at=clock_timestamp()
 FROM public.host_devices d WHERE c.device_id=p_device AND d.id=c.device_id AND d.state='active'
   AND c.epoch=p_epoch AND c.gateway_instance=p_gateway AND c.lease_until>clock_timestamp();
 RETURN FOUND;
END $$;

CREATE FUNCTION public.host_connection_close(p_device uuid,p_epoch uuid,p_gateway text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 DELETE FROM public.host_connections WHERE device_id=p_device AND epoch=p_epoch AND gateway_instance=p_gateway;
 RETURN FOUND;
END $$;

-- Directory presence is not authority. This is the bounded read admission for
-- the owner's own files, not a Project grant and not a remote shell grant.
CREATE FUNCTION public.host_folder_admit(p_actor uuid,p_folder uuid,p_operation text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE admitted jsonb;
BEGIN
 IF p_operation IS NULL OR p_operation NOT IN ('list','read','write','create','move','remove','mkdir','watch') THEN
   RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object('device_id',d.id,'folder_id',f.id,'epoch',c.epoch,
   'gateway_instance',c.gateway_instance,'device_revision',d.revision,
   'folder_revision',f.revision,'lease_until',c.lease_until) INTO admitted
 FROM public.host_folders f JOIN public.host_devices d ON d.id=f.device_id
 JOIN public.host_connections c ON c.device_id=d.id
 WHERE f.id=p_folder AND d.owner_user_id=p_actor AND d.state='active' AND f.state='active'
   AND c.lease_until>clock_timestamp() AND
   (CASE WHEN p_operation IN ('list','read','watch') THEN 'folder.read' ELSE 'folder.write' END)=ANY(f.capabilities);
 IF admitted IS NULL THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 RETURN admitted;
END $$;

-- Register from an authenticated human session. Possession of the private key
-- is checked separately at connection time; registration alone grants no file access.
CREATE FUNCTION public.host_device_register(p_owner uuid,p_key bytea,p_name text,p_platform text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result uuid;
BEGIN
 IF p_owner IS NULL OR p_key IS NULL OR octet_length(p_key)<>32 OR
   p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 1 AND 120 OR
   p_platform IS NULL OR p_platform NOT IN ('darwin','win32','linux') THEN
   RAISE EXCEPTION 'host_invalid_device' USING ERRCODE='22023';
 END IF;
 -- Serialize registration count for this user, not across all tenants.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_owner::text,72343));
 IF (SELECT count(*) FROM public.host_devices WHERE owner_user_id=p_owner AND state<>'revoked')>=16 THEN
   RAISE EXCEPTION 'host_device_limit' USING ERRCODE='22023';
 END IF;
 INSERT INTO public.host_devices(owner_user_id,public_key,display_name,platform)
 VALUES(p_owner,p_key,btrim(p_name),p_platform) RETURNING id INTO result;
 RETURN result;
END $$;

CREATE FUNCTION public.host_folder_register(p_owner uuid,p_device uuid,p_name text,p_capabilities text[])
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result uuid;
BEGIN
 IF p_owner IS NULL OR p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 1 AND 120 OR
   p_capabilities IS NULL OR NOT (p_capabilities <@ ARRAY['folder.read','folder.write']::text[]) OR
   NOT ('folder.read'=ANY(p_capabilities)) OR cardinality(p_capabilities) NOT BETWEEN 1 AND 2 OR
   array_position(p_capabilities,NULL) IS NOT NULL THEN
   RAISE EXCEPTION 'host_invalid_folder' USING ERRCODE='22023';
 END IF;
 PERFORM 1 FROM public.host_devices WHERE id=p_device AND owner_user_id=p_owner AND state='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 IF (SELECT count(*) FROM public.host_folders WHERE device_id=p_device AND state<>'removed')>=64 THEN
   RAISE EXCEPTION 'host_folder_limit' USING ERRCODE='22023';
 END IF;
 INSERT INTO public.host_folders(device_id,display_name,capabilities)
 VALUES(p_device,btrim(p_name),p_capabilities) RETURNING id INTO result;
 RETURN result;
END $$;

CREATE FUNCTION public.host_device_catalog(p_owner uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('id',d.id,'name',d.display_name,
   'platform',d.platform,'state',d.state,'revision',d.revision,
   'online',coalesce(c.lease_until>clock_timestamp(),false),'last_seen_at',d.last_seen_at,
   'folders',coalesce((SELECT jsonb_agg(jsonb_build_object('id',f.id,'name',f.display_name,
     'capabilities',f.capabilities,'state',f.state,'revision',f.revision) ORDER BY f.created_at,f.id)
     FROM public.host_folders f WHERE f.device_id=d.id AND f.state<>'removed'),'[]'::jsonb))
   ORDER BY d.created_at,d.id),'[]'::jsonb)
 FROM public.host_devices d LEFT JOIN public.host_connections c ON c.device_id=d.id
 WHERE d.owner_user_id=p_owner AND d.state<>'revoked'
$$;

CREATE FUNCTION public.host_device_revoke(p_owner uuid,p_device uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 UPDATE public.host_devices SET state='revoked',revision=revision+1,updated_at=clock_timestamp()
 WHERE id=p_device AND owner_user_id=p_owner AND state<>'revoked';
 IF NOT FOUND THEN RETURN false; END IF;
 DELETE FROM public.host_connections WHERE device_id=p_device;
 RETURN true;
END $$;

CREATE FUNCTION public.host_folder_remove(p_owner uuid,p_device uuid,p_folder uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 UPDATE public.host_folders f SET state='removed',revision=f.revision+1,updated_at=clock_timestamp()
 FROM public.host_devices d WHERE f.id=p_folder AND f.device_id=p_device AND d.id=f.device_id
 AND d.owner_user_id=p_owner AND d.state='active' AND f.state<>'removed';
 RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION public.host_device_register(uuid,bytea,text,text),
 public.host_folder_register(uuid,uuid,text,text[]),public.host_device_catalog(uuid),
 public.host_device_revoke(uuid,uuid),public.host_folder_remove(uuid,uuid,uuid)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_device_register(uuid,bytea,text,text),
 public.host_folder_register(uuid,uuid,text,text[]),public.host_device_catalog(uuid),
 public.host_device_revoke(uuid,uuid),public.host_folder_remove(uuid,uuid,uuid)
 TO service_role;

REVOKE ALL ON FUNCTION public.host_connection_open(uuid,uuid,text,integer),
 public.host_connection_renew(uuid,uuid,text,integer),public.host_connection_close(uuid,uuid,text),
 public.host_folder_admit(uuid,uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_connection_open(uuid,uuid,text,integer),
 public.host_connection_renew(uuid,uuid,text,integer),public.host_connection_close(uuid,uuid,text),
 public.host_folder_admit(uuid,uuid,text) TO service_role;
COMMIT;
