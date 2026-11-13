-- Native repository discovery for stored projects that were never enrolled.
-- Run only on disposable test databases; all synthetic writes roll back.
BEGIN;
SELECT no_plan();
\ir _support/data_api_containment_fixture.inc

SELECT has_function('public', 'get_version_repository_snapshot', ARRAY['text'],
    'repository authority discovery is installed');
SELECT is(public.get_version_repository_snapshot('issue053-project-a'), NULL::jsonb,
    'an existing project without native enrollment has an explicit absent snapshot');
SELECT is((SELECT count(*) FROM public.version_repositories), 0::bigint,
    'Expand does not enroll existing projects');

SET LOCAL ROLE anon;
SELECT throws_ok($sql$SELECT public.get_version_repository_snapshot('issue053-project-a')$sql$,
    '42501', 'permission denied for function get_version_repository_snapshot',
    'anonymous callers cannot inspect repository authority');
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT throws_ok($sql$SELECT public.get_version_repository_snapshot('issue053-project-a')$sql$,
    '42501', 'permission denied for function get_version_repository_snapshot',
    'direct authenticated clients cannot inspect repository authority');
RESET ROLE;

SET LOCAL ROLE service_role;
SELECT is(public.get_version_repository_snapshot('issue053-project-a'), NULL::jsonb,
    'the backend can discover absent native authority instead of receiving a missing RPC');
RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
