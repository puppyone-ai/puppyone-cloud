-- A host stays online with its own device key, without a signed-in person on it.
-- The gateway verifies a device-signed challenge request against the public key
-- this returns, then issues the usual one-use challenge. Additive; no data is
-- rewritten and no table privilege changes.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

-- Only an active device of this owner has a key to check; anything else is a
-- definite denial, which the host treats as revocation.
CREATE FUNCTION public.host_device_key(p_owner uuid,p_device uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 SELECT jsonb_build_object('public_key',encode(public_key,'hex'),'revision',revision) INTO result
 FROM public.host_devices WHERE id=p_device AND owner_user_id=p_owner AND state='active';
 IF result IS NULL THEN RAISE EXCEPTION 'host_access_denied' USING ERRCODE='42501'; END IF;
 RETURN result;
END $$;

REVOKE ALL ON FUNCTION public.host_device_key(uuid,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.host_device_key(uuid,uuid) TO service_role;

COMMIT;
