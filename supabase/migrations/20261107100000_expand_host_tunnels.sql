-- SSH tunnels to a folder's host: the gateway relays bytes it cannot read and
-- records only that a tunnel opened and how it ended. Additive; no data is
-- rewritten, no privileges change.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

ALTER TABLE public.host_file_events DROP CONSTRAINT host_file_events_operation_check;
ALTER TABLE public.host_file_events ADD CONSTRAINT host_file_events_operation_check CHECK (
  operation IN ('list','read','write','create','move','remove','mkdir','watch','shell','git','agent','tunnel'));

CREATE OR REPLACE FUNCTION public.host_file_event_record(p_actor uuid,p_device uuid,p_folder uuid,p_operation text,p_outcome text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_operation IS NULL OR p_outcome IS NULL OR
   p_operation NOT IN ('list','read','write','mkdir','move','remove','watch','shell','git','agent','tunnel') OR
   p_outcome NOT IN ('intended','succeeded','failed','unknown') THEN
  RAISE EXCEPTION 'host_invalid_audit' USING ERRCODE='22023'; END IF;
 INSERT INTO public.host_file_events(device_id,folder_id,actor_user_id,operation,outcome)
 SELECT d.id,f.id,p_actor,p_operation,p_outcome FROM public.host_devices d
 JOIN public.host_folders f ON f.device_id=d.id
 WHERE d.id=p_device AND d.owner_user_id=p_actor AND f.id=p_folder;
 IF NOT FOUND THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 RETURN true;
END $$;

REVOKE ALL ON FUNCTION public.host_file_event_record(uuid,uuid,uuid,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_file_event_record(uuid,uuid,uuid,text,text) TO service_role;

COMMIT;
