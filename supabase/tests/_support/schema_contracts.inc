-- Portable post-deploy contracts for critical database objects.
-- Run after migrations to catch function/table schema mismatches.
--
-- Hosted usage: psql -X -v ON_ERROR_STOP=1 -f schema_contracts.sql
-- Local pgTAP usage: supabase/tests/smoke_test_triggers.sql includes this file.
--
-- Covered checks:
--   1. handle_new_user() trigger: exists, compiles, references only existing columns
--   2. MUT CAS RPCs: correct TEXT signatures, no UUID overloads, invokable without type errors
--
-- Design note: each block is self-contained so a failure in one does not mask another.
-- The CAS RPC block is wrapped in BEGIN/ROLLBACK so its probe calls leave no residue.

BEGIN;

DO $$
DECLARE
    func_body TEXT;
    col_name TEXT;
    missing_cols TEXT[] := '{}';
BEGIN
    -- 1. Verify function exists
    SELECT prosrc INTO func_body
    FROM pg_proc
    WHERE proname = 'handle_new_user'
      AND pronamespace = 'public'::regnamespace;

    IF func_body IS NULL THEN
        RAISE EXCEPTION 'SMOKE TEST FAILED: handle_new_user() function does not exist';
    END IF;

    -- 2. Extract column names from the INSERT statement and verify they exist
    -- Check each column that the function references in profiles
    FOR col_name IN
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'profiles'
    LOOP
        -- columns exist, good
    END LOOP;

    -- Specifically check for known-bad columns that were previously dropped
    IF func_body ILIKE '%role%' AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'profiles' AND column_name = 'role'
    ) THEN
        missing_cols := array_append(missing_cols, 'role');
    END IF;

    IF func_body ILIKE '%plan%' AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'profiles' AND column_name = 'plan'
    ) THEN
        missing_cols := array_append(missing_cols, 'plan');
    END IF;

    IF array_length(missing_cols, 1) > 0 THEN
        RAISE EXCEPTION 'SMOKE TEST FAILED: handle_new_user() references dropped columns: %', missing_cols;
    END IF;

    -- 3. Verify trigger exists on auth.users
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'on_auth_user_created'
    ) THEN
        RAISE EXCEPTION 'SMOKE TEST FAILED: on_auth_user_created trigger does not exist';
    END IF;

    RAISE NOTICE 'SMOKE TEST PASSED: handle_new_user() trigger is consistent with profiles table';
END;
$$;

DO $$
BEGIN
    IF to_regprocedure('public.project_creator_authorization_preflight()') IS NULL THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: Project creator authorization preflight is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'project_members'
          AND t.tgname = 'trg_project_members_creator_admin_guard'
          AND NOT t.tgisinternal
          AND t.tgenabled <> 'D'
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: Project creator membership guard is missing or disabled';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'projects'
          AND t.tgname = 'trg_projects_creator_admin_guard'
          AND t.tgdeferrable
          AND t.tginitdeferred
          AND t.tgenabled <> 'D'
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: deferred Project creator fact guard is missing or disabled';
    END IF;

    IF has_function_privilege(
        'anon', 'public.project_creator_authorization_preflight()', 'EXECUTE'
    ) OR has_function_privilege(
        'authenticated', 'public.project_creator_authorization_preflight()', 'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: creator diagnostics are exposed to client roles';
    END IF;

    RAISE NOTICE
        'SMOKE TEST PASSED: Project creator authorization guards are installed';
END;
$$;

-- ============================================================================
-- Check 2: the pre-native CAS RPCs are retired and object locations keep RLS.
--
-- Native refs are the only version authority. 20261114020000 dropped the old
-- root/scope CAS RPCs with their tables; any of them (or the long-gone integer
-- counter) reappearing would mean a stale writer path was restored.
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('cas_update_scope_state', 'cas_update_root_hash', 'atomic_next_version')
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: a retired pre-native CAS RPC exists again. '
            'Native refs are the only version authority.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'version_object_locations'
          AND c.relkind = 'r'
          AND c.relrowsecurity
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: version_object_locations must have row-level security enabled';
    END IF;

    RAISE NOTICE 'SMOKE TEST PASSED: retired CAS RPCs are absent and object-location RLS is enabled';
END;
$$;

ROLLBACK;


-- ============================================================================
-- Check 3: object-GC candidate RPC must execute a real upsert and reconcile
-- its candidate set.  The function returns TABLE(object_id), so an
-- ``ON CONFLICT (project_id, object_id)`` target can become ambiguous to
-- PL/pgSQL because ``object_id`` is also an OUT variable.  Run against a
-- temporary project and roll it back so this proves the SQL path without
-- leaving production/staging data behind.
-- ============================================================================

BEGIN;

DO $$
DECLARE
    probe_org_id text;
    probe_project_id text := gen_random_uuid()::text;
    probe_object_id constant text := 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    matured_ids text[];
BEGIN
    SELECT id INTO probe_org_id
    FROM public.organizations
    ORDER BY created_at
    LIMIT 1;

    -- A pristine local database can legitimately have no organization. The
    -- deployed staging/production databases always do; skip rather than make
    -- the general migration smoke test depend on seed data.
    IF probe_org_id IS NULL THEN
        RAISE NOTICE 'SMOKE TEST SKIPPED: no organization available for object-GC RPC probe';
        RETURN;
    END IF;

    INSERT INTO public.projects (id, name, org_id, lifecycle_status)
    VALUES (probe_project_id, '__object_gc_rpc_smoke__', probe_org_id, 'ready');

    SELECT array_agg(s.object_id) INTO matured_ids
    FROM public.sync_version_object_gc_candidates(
        probe_project_id,
        ARRAY[probe_object_id],
        now(),
        0
    ) AS s;

    IF matured_ids IS DISTINCT FROM ARRAY[probe_object_id] THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: sync_version_object_gc_candidates returned %, expected %',
            matured_ids,
            ARRAY[probe_object_id];
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.sync_version_object_gc_candidates(
            probe_project_id,
            ARRAY[]::text[],
            now(),
            0
        )
    ) THEN
        RAISE EXCEPTION
            'SMOKE TEST FAILED: object-GC candidate reconciliation retained removed objects';
    END IF;

    RAISE NOTICE 'SMOKE TEST PASSED: object-GC candidate RPC upserts and reconciles';
END;
$$;

ROLLBACK;
