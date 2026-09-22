BEGIN;
SELECT plan(3);
SELECT ok((SELECT relrowsecurity FROM pg_class WHERE oid='public.scope_sandbox_sessions'::regclass), 'Sandbox state uses RLS');
SELECT ok(NOT has_table_privilege('anon','public.scope_sandbox_sessions','SELECT,INSERT,UPDATE,DELETE'), 'Anonymous clients cannot access internal sandbox state');
SELECT ok(NOT has_table_privilege('authenticated','public.scope_sandbox_sessions','SELECT,INSERT,UPDATE,DELETE'), 'Human clients cannot access internal sandbox state directly');
SELECT * FROM finish();
ROLLBACK;
