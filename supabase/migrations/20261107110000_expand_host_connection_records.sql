-- Connection records for people's own computers are kept 30 days. File
-- operations there are no longer recorded one by one; what remains (opening a
-- folder, tunnels, terminal and Agent sessions) carries no names, paths or
-- content. Each new record also removes a bounded batch of expired ones, so
-- retention needs no scheduler. Additive: an index and a replaced function.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

CREATE INDEX host_file_events_created_at ON public.host_file_events(created_at, id);

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
 DELETE FROM public.host_file_events WHERE id IN (
   SELECT id FROM public.host_file_events WHERE created_at < clock_timestamp() - interval '30 days'
   ORDER BY created_at, id LIMIT 500);
 RETURN true;
END $$;

REVOKE ALL ON FUNCTION public.host_file_event_record(uuid,uuid,uuid,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_file_event_record(uuid,uuid,uuid,text,text) TO service_role;

COMMIT;
