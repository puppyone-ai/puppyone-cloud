\ir _support/data_api_containment.inc
BEGIN;
SELECT no_plan();
\ir _support/data_api_containment_fixture.inc

SELECT is((SELECT count(*) FROM public.audit_logs WHERE project_id LIKE 'issue053-project-%'),
    2::bigint, 'nonempty audit fixture includes both tenants');

-- Each SQL denial must be 42501, not an empty relation, missing column, bad
-- request or a service-role client accidentally used for a negative test.
CREATE FUNCTION pg_temp.expect_client_denials(client text) RETURNS SETOF text
LANGUAGE plpgsql AS $$
DECLARE
    relation text;
    column_name text;
    operation text;
    seq text;
    fn record;
BEGIN
    EXECUTE format('SET LOCAL ROLE %I', client);
    FOREACH relation IN ARRAY ARRAY[
        'audit_logs', 'tables'
    ] LOOP
        column_name := 'project_id';
        RETURN NEXT throws_ok(format('SELECT * FROM public.%I', relation), '42501',
            format('permission denied for table %s', relation), client || ' cannot read ' || relation);
        RETURN NEXT throws_ok(format('INSERT INTO public.%I DEFAULT VALUES', relation), '42501',
            format('permission denied for table %s', relation), client || ' cannot insert ' || relation);
        RETURN NEXT throws_ok(format('UPDATE public.%I SET %I = %I', relation, column_name, column_name), '42501',
            format('permission denied for table %s', relation), client || ' cannot update ' || relation);
        RETURN NEXT throws_ok(format('DELETE FROM public.%I', relation), '42501',
            format('permission denied for table %s', relation), client || ' cannot delete ' || relation);
        RETURN NEXT ok(NOT has_table_privilege(client, 'public.' || relation, 'TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'),
            client || ' has no whole-table or DDL privileges on ' || relation);
    END LOOP;
    FOREACH seq IN ARRAY ARRAY['audit_logs_id_seq'] LOOP
        RETURN NEXT throws_ok(format('SELECT nextval(%L)', 'public.' || seq), '42501',
            'permission denied for sequence ' || seq, client || ' cannot advance ' || seq);
        RETURN NEXT throws_ok(format('SELECT setval(%L, 900)', 'public.' || seq), '42501',
            'permission denied for sequence ' || seq, client || ' cannot reset ' || seq);
        RETURN NEXT ok(NOT has_sequence_privilege(client, 'public.' || seq, 'SELECT'), client || ' cannot inspect ' || seq);
    END LOOP;
    FOR fn IN
        SELECT p.proname, string_agg('NULL::' || t.typ::regtype, ', ' ORDER BY t.position) AS arguments
        FROM pg_proc p
        CROSS JOIN LATERAL unnest(p.proargtypes) WITH ORDINALITY t(typ, position)
        WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(ARRAY[
            'add_project_member_authorized', 'remove_project_member_authorized',
            'update_project_member_role_authorized', 'join_project_via_share_token',
            'issue_user_git_http_credential', 'issue_user_git_http_credential_idempotent',
            'revoke_user_git_http_credential',
            '_project_initialization_has_cascade_dependents', 'delete_project_control_plane',
            'abandon_project_initialization'
        ]) GROUP BY p.oid, p.proname
    LOOP
        RETURN NEXT throws_ok(format('SELECT public.%I(%s)', fn.proname, fn.arguments), '42501',
            'permission denied for function ' || fn.proname, client || ' cannot call ' || fn.proname);
    END LOOP;
    RETURN NEXT throws_ok('SELECT public.repository_target_integrity_report()', '42501',
        'permission denied for function repository_target_integrity_report', client || ' cannot call diagnostics');
    RESET ROLE;
END $$;
SELECT * FROM pg_temp.expect_client_denials('anon');
SELECT * FROM pg_temp.expect_client_denials('authenticated');

-- A mistaken future table grant still cannot bypass RLS. Roll back these
-- intentional negative mutations with the fixture.
GRANT SELECT, INSERT ON public.tables TO anon, authenticated;
SET LOCAL ROLE anon;
SELECT is((SELECT count(*) FROM public.tables), 0::bigint, 'RLS denies anon even after accidental table grant');
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000053","role":"authenticated"}', true);
SELECT is((SELECT count(*) FROM public.tables), 0::bigint, 'RLS denies user even on their own tenant');
SELECT throws_ok($sql$INSERT INTO public.tables(id,project_id) VALUES ('rls-probe','issue053-project-a')$sql$,
    '42501', 'new row violates row-level security policy for table "tables"', 'RLS denies client insert');
RESET ROLE;

SET LOCAL ROLE service_role;
SELECT is((SELECT count(*) FROM public.audit_logs WHERE project_id LIKE 'issue053-project-%'),
    2::bigint, 'backend can still read both tenant records after application authorization');
SELECT lives_ok($sql$INSERT INTO public.audit_logs(project_id,action) VALUES ('issue053-project-a','backend-probe')$sql$,
    'backend appends audit with sequence USAGE');
SELECT lives_ok($sql$INSERT INTO public.tables(id,project_id,data) VALUES ('backend-table','issue053-project-a','{}');
    UPDATE public.tables SET data='{"saved":true}' WHERE id='backend-table';
    DELETE FROM public.tables WHERE id='backend-table'$sql$, 'backend structured table CRUD');
SELECT ok(NOT has_table_privilege('service_role','public.audit_logs','UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES,MAINTAIN'),
    'audit backend retains only append/read permissions');
SELECT ok(NOT has_sequence_privilege('service_role','public.audit_logs_id_seq','UPDATE'), 'backend cannot reset audit sequence');
RESET ROLE;
SELECT * FROM finish();
ROLLBACK;
