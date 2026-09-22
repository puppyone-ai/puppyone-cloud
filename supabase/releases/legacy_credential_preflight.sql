-- Read-only admission check for the ordinary schema deployment lane.
-- The historical credential retirement cannot create its own application-key
-- hashes. Reject an unprepared upgrade before applying ANY compatible DDL.
-- Preparing legacy data belongs to the governed data-migration lane.
BEGIN READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $$
DECLARE
    has_raw_key boolean;
    has_hash boolean;
    pending_count bigint;
BEGIN
    IF to_regclass('public.repo_scopes') IS NOT NULL THEN
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'repo_scopes'
              AND column_name = 'access_key'
        ) INTO has_raw_key;
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'repo_scopes'
              AND column_name = 'access_key_hash'
        ) INTO has_hash;

        IF has_raw_key AND has_hash THEN
            EXECUTE 'SELECT count(*) FROM public.repo_scopes '
                    'WHERE access_key IS NOT NULL AND access_key_hash IS NULL'
                INTO pending_count;
        ELSIF has_raw_key THEN
            EXECUTE 'SELECT count(*) FROM public.repo_scopes WHERE access_key IS NOT NULL'
                INTO pending_count;
        ELSE
            pending_count := 0;
        END IF;

        IF pending_count > 0 THEN
            RAISE EXCEPTION
                'LEGACY_CREDENTIAL_UPGRADE_REQUIRED: % Scope credentials require the governed hash migration before schema deployment',
                pending_count;
        END IF;
    END IF;

    IF to_regclass('public.access_surfaces') IS NOT NULL THEN
        SELECT count(*) INTO pending_count
        FROM public.access_surfaces
        WHERE kind IN ('agent', 'sandbox')
          AND (config ? 'api_key' OR config ? 'mcp_api_key' OR config ? 'access_key');
        IF pending_count > 0 THEN
            RAISE EXCEPTION
                'LEGACY_CREDENTIAL_UPGRADE_REQUIRED: % Agent/Sandbox configurations require the governed credential migration before schema deployment',
                pending_count;
        END IF;
    END IF;
END;
$$;

ROLLBACK;
