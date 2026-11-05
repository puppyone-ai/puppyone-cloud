#!/usr/bin/env bash
# Disposable schema + authorization regression. Never connects to a hosted database.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
container="puppyone-device-host-test-$$"
docker run --rm -d --name "$container" -e POSTGRES_PASSWORD=fixture postgres:16 >/dev/null
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
ready=false
for _ in $(seq 1 60); do
  if docker exec "$container" pg_isready -U postgres >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
if [[ "$ready" != true ]]; then echo 'Disposable PostgreSQL did not become ready' >&2; exit 1; fi
docker exec -i "$container" psql -U postgres -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY);
SQL
docker exec -i "$container" psql -U postgres -v ON_ERROR_STOP=1 -q \
  < "$root/supabase/migrations/20261105120000_expand_device_hosts.sql"
docker exec -i "$container" psql -U postgres -v ON_ERROR_STOP=1 -q <<'SQL'
INSERT INTO auth.users VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  ('dddddddd-dddd-4ddd-8ddd-dddddddddddd');
INSERT INTO public.host_devices(id,owner_user_id,public_key,display_name,platform)
VALUES ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',decode(repeat('ab',32),'hex'),'Mac','darwin');
INSERT INTO public.host_folders(id,device_id,display_name) VALUES
('cccccccc-cccc-4ccc-8ccc-cccccccccccc','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','Notes');
DO $$ DECLARE
 device uuid:='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
 folder uuid:='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
 owner uuid:='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
 stranger uuid:='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
 old_epoch uuid:='ffffffff-ffff-4fff-8fff-ffffffffffff';
 new_epoch uuid:='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
BEGIN
 DECLARE
   challenge uuid;
 BEGIN
   challenge:=public.host_challenge_issue(owner,device,old_epoch,decode(repeat('12',32),'hex'));
   IF public.host_challenge_consume(owner,device,challenge,old_epoch,decode(repeat('12',32),'hex'))->>'public_key'
      <>repeat('ab',32) THEN RAISE EXCEPTION 'challenge proof key mismatch'; END IF;
   BEGIN PERFORM public.host_challenge_consume(owner,device,challenge,old_epoch,decode(repeat('12',32),'hex'));
     RAISE EXCEPTION 'challenge replay accepted';
   EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 END;
 PERFORM public.host_connection_open(device,old_epoch,'gateway-1',30);
 PERFORM public.host_connection_open(device,new_epoch,'gateway-2',30);
 IF public.host_connection_renew(device,old_epoch,'gateway-1',30) THEN
   RAISE EXCEPTION 'old epoch renewed'; END IF;
 IF public.host_connection_close(device,old_epoch,'gateway-1') THEN
   RAISE EXCEPTION 'old epoch closed new slot'; END IF;
 IF (public.host_folder_admit(owner,folder,'read')->>'epoch')::uuid<>new_epoch THEN
   RAISE EXCEPTION 'wrong epoch'; END IF;
 PERFORM public.host_file_event_record(owner,device,folder,'read','intended');
 IF (SELECT count(*) FROM public.host_file_events WHERE actor_user_id=owner AND outcome='intended')<>1
   THEN RAISE EXCEPTION 'audit missing'; END IF;
 BEGIN PERFORM public.host_file_event_record(stranger,device,folder,'read','intended');
   RAISE EXCEPTION 'foreign audit accepted';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 IF has_function_privilege('authenticated','public.host_file_event_record(uuid,uuid,uuid,text,text)','EXECUTE')
   THEN RAISE EXCEPTION 'public audit RPC'; END IF;
 BEGIN PERFORM public.host_folder_admit(stranger,folder,'read');
   RAISE EXCEPTION 'cross-account admitted';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 BEGIN PERFORM public.host_folder_admit(owner,folder,'write');
   RAISE EXCEPTION 'write admitted';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 IF has_function_privilege('authenticated','public.host_folder_admit(uuid,uuid,text)','EXECUTE')
   THEN RAISE EXCEPTION 'public RPC'; END IF;
 IF has_table_privilege('service_role','public.host_devices','SELECT')
   THEN RAISE EXCEPTION 'direct table read'; END IF;
 UPDATE public.host_devices SET state='revoked' WHERE id=device;
 IF public.host_connection_renew(device,new_epoch,'gateway-2',30)
   THEN RAISE EXCEPTION 'revoked renewal'; END IF;
 BEGIN PERFORM public.host_folder_admit(owner,folder,'read');
   RAISE EXCEPTION 'revoked read admitted';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
DO $$ DECLARE
 owner uuid:='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
 stranger uuid:='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
 device uuid; folder uuid;
BEGIN
 device:=public.host_device_register(owner,decode(repeat('cd',32),'hex'),'Laptop','darwin');
 IF public.host_device_register(owner,decode(repeat('cd',32),'hex'),'Laptop','darwin')<>device THEN
   RAISE EXCEPTION 'device retry changed identity'; END IF;
 folder:=gen_random_uuid();
 IF public.host_folder_register(owner,device,folder,'Work',ARRAY['folder.read','folder.write'])<>folder
   OR public.host_folder_register(owner,device,folder,'Work',ARRAY['folder.read','folder.write'])<>folder THEN
   RAISE EXCEPTION 'folder retry changed identity'; END IF;
 IF jsonb_array_length(public.host_device_catalog(owner))<>1 THEN RAISE EXCEPTION 'catalog missing device'; END IF;
 IF jsonb_array_length(public.host_device_catalog(stranger))<>0 THEN RAISE EXCEPTION 'catalog leaked device'; END IF;
 BEGIN PERFORM public.host_folder_register(stranger,device,gen_random_uuid(),'No',ARRAY['folder.read']);
   RAISE EXCEPTION 'foreign folder registration';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 IF public.host_folder_remove(stranger,device,folder) THEN RAISE EXCEPTION 'foreign folder removal'; END IF;
 IF NOT public.host_folder_remove(owner,device,folder) THEN RAISE EXCEPTION 'folder removal failed'; END IF;
 IF NOT public.host_device_revoke(owner,device) THEN RAISE EXCEPTION 'device revocation failed'; END IF;
 BEGIN PERFORM public.host_connection_open(device,gen_random_uuid(),'gateway-3',30);
   RAISE EXCEPTION 'revoked device connected';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SQL
printf '%s\n' 'Device host schema and access regression passed.'
