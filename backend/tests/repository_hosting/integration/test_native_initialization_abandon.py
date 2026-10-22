"""Real PG cancellation and publication races; not physical S3 purge proof."""
import importlib.util
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from tests.repository_hosting.harness.postgres import Postgres, literal
from tests.repository_hosting.harness.ref_authority import A, oid, update
from tests.repository_hosting.integration.test_object_location_fence import location
from tests.repository_hosting.integration.test_publication_pins import rpc

pytestmark = pytest.mark.hosting_live
ROOT = Path(__file__).resolve().parents[4]
EXPAND = ROOT / 'supabase/migrations/20261022040000_fix_native_initialization_abandon.sql'


def create(pg, object_format='sha1'):
    # This owned SQL-only fixture has no physical object service to inventory.
    pg.sql("SELECT public.finalize_project_storage_inventory_scan(0,0,repeat('a',64));"
           "SELECT public.verify_project_storage_inventory(0,0,repeat('a',64));"
           "SELECT public.complete_project_storage_inventory();")
    seed = pg.create_project()
    owner = json.loads(pg.value(
        "SELECT jsonb_build_object('org',org_id,'user',created_by) FROM public.projects WHERE id=" + literal(seed)
    ))
    pg.sql('DELETE FROM public.projects WHERE id=' + literal(seed))
    project, key = 'cancel-' + uuid.uuid4().hex, str(uuid.uuid4())
    pg.sql("INSERT INTO public.organization_entitlements(org_id,source,source_revision,payload_hash,entitlements) VALUES("
           + ','.join(map(literal, (owner['org'], 'puppypay', 1, 'a'*64,
               {'limits': {'storage.max_bytes': 10000000, 'upload.max_single_file_bytes': 1000000}}))) + ')')
    outcome = json.loads(rpc(pg, 'create_native_project_repository', key, 'a'*64, project,
        'Cancellation test', '', owner['org'], owner['user'], 'share-'+key, 'empty',
        None, None, {}, object_format, 'main').stdout)
    assert outcome['outcome'] == 'native_created'
    return SimpleNamespace(pg=pg, project=project, key=key, **owner)


@pytest.fixture
def native_empty():
    a = create(Postgres())
    try:
        yield a
    finally:
        a.pg.sql('DELETE FROM public.projects WHERE id=' + literal(a.project))


def abandon(a):
    return json.loads(rpc(a.pg, 'abandon_project_initialization', a.project, a.key, a.user).stdout)


def publication(a):
    pin, io = str(uuid.uuid4()), str(uuid.uuid4())
    a.lease, a.holder, a.actor = str(uuid.uuid4()), 'discard-test', 'user:' + a.user
    rpc(a.pg, 'acquire_project_write_lease', a.project, a.lease, a.holder, 'git.receive-pack', 120)
    rpc(a.pg, 'begin_admitted_version_object_publication', a.project, a.actor, pin,
        1, {A: 'commit'}, a.lease, a.holder)
    rpc(a.pg, 'reserve_version_object_capacity', a.project, a.actor, pin,
        [{'object_id': A, 'object_kind': 'commit', 'body_bytes': 6}], True, io)
    rpc(a.pg, 'register_version_object_locations', a.project, a.actor, pin, [location(a.project)])
    rpc(a.pg, 'settle_version_object_capacity_io', a.project, a.actor, pin, io)
    rpc(a.pg, 'seal_capacity_version_object_publication', a.project, a.actor, pin,
        'f'*64, {A: {'kind': 'commit', 'peeled_oid': None}})
    return pin


def publish(a, pin, edits=None, *, check=True):
    edits = edits or [update(new=oid(A))]
    before = a.pg.value("SELECT target_oid FROM public.version_repository_refs WHERE project_id="
                        + literal(a.project) + " AND name=convert_to('refs/heads/main','UTF8')") or None
    after = before
    for edit in edits:
        if edit['name_b64'] == update()['name_b64']:
            after = edit.get('new', {}).get('oid')
    measured = dict(org_id=a.org, source_revision=1, old_head_oid=before,
                    new_head_oid=after, old_bytes=0, new_bytes=0)
    policy = dict(org_id=a.org, source_revision=1, file_limit=1000000,
                  old_head_oid=before, new_head_oid=after, manifest_sha256='f'*64)
    return rpc(a.pg, 'apply_policy_version_ref_transaction', a.project, a.actor,
               str(uuid.uuid4()), 1, edits, pin, 'test publish', a.lease, a.holder,
               measured, policy, check=check)


def lifecycle(a):
    return a.pg.value('SELECT lifecycle_status FROM public.projects WHERE id=' + literal(a.project))


@pytest.mark.parametrize('object_format', ['sha1', 'sha256'])
def test_new_native_project_can_be_abandoned_and_replayed(object_format):
    a = create(Postgres(), object_format)
    try:
        accepted = abandon(a)
        assert accepted['outcome'] == 'accepted'
        assert lifecycle(a) == 'deleting'
        replay = abandon(a)
        assert replay['outcome'] == 'replayed'
        assert replay['job']['id'] == accepted['job']['id']
        assert replay['job']['object_prefixes'][0] == f'version/{a.project}/'
        denied = rpc(a.pg, 'begin_version_object_publication', a.project,
                     'test:writer', str(uuid.uuid4()), 1, {A: 'commit'}, check=False)
        assert denied.returncode and 'repository_unavailable' in denied.stderr
    finally:
        a.pg.sql('DELETE FROM public.projects WHERE id=' + literal(a.project))


def test_interrupted_objects_pins_and_write_lease_are_cleanup_inputs(native_empty):
    a = native_empty
    pin = publication(a)
    late_pin, io = str(uuid.uuid4()), str(uuid.uuid4())
    rpc(a.pg, 'begin_admitted_version_object_publication', a.project, a.actor, late_pin,
        1, {A: 'commit'}, a.lease, a.holder)
    rpc(a.pg, 'reserve_version_object_capacity', a.project, a.actor, late_pin,
        [{'object_id': 'b'*40, 'object_kind': 'commit', 'body_bytes': 3}], True, io)
    accepted = abandon(a)
    assert accepted['outcome'] == 'accepted'
    assert lifecycle(a) == 'deleting'
    job = accepted['job']['id']
    a.pg.sql("UPDATE public.project_deletion_jobs SET status='running',claimed_by='cancel-worker',claimed_at=now() WHERE id=" + literal(job))
    waiting = json.loads(rpc(a.pg, 'drain_project_deletion_job', job, 'cancel-worker').stdout)
    assert waiting == {'outcome': 'waiting', 'reason': 'native_storage_io_unsettled'}
    assert json.loads(rpc(a.pg, 'settle_version_object_capacity_io', a.project, a.actor, late_pin, io).stdout)['settled_objects'] == 1
    assert a.pg.value('SELECT used_body_bytes FROM public.version_repository_capacity WHERE project_id=' + literal(a.project)) == '9'
    assert a.pg.value('SELECT count(*) FROM public.version_object_locations WHERE project_id=' + literal(a.project)) == '1'
    denied = publish(a, pin, check=False)
    assert denied.returncode and 'repository_unavailable' in denied.stderr
    assert a.pg.value('SELECT ref_sequence FROM public.version_repositories WHERE project_id=' + literal(a.project)) == '0'


@pytest.mark.parametrize('identity', ['actor', 'operation', 'project'])
def test_discard_cannot_escape_original_create_identity(native_empty, identity):
    a = native_empty
    project, key, user = a.project, a.key, a.user
    if identity == 'actor':
        user = str(uuid.uuid4())
    elif identity == 'operation':
        key = str(uuid.uuid4())
    else:
        project = 'other-project'
    denied = json.loads(rpc(a.pg, 'abandon_project_initialization', project, key, user).stdout)
    assert denied['outcome'] in {'not_found', 'conflict'}
    assert lifecycle(a) == 'ready'


@pytest.mark.parametrize('state', ['branch', 'tag', 'deleted-ref', 'project-changed'])
def test_user_content_or_changed_project_cannot_be_discarded(native_empty, state):
    a = native_empty
    pin = publication(a)
    if state == 'project-changed':
        a.pg.sql("UPDATE public.projects SET name='Changed by user' WHERE id=" + literal(a.project))
    else:
        name = b'refs/tags/v1' if state == 'tag' else b'refs/heads/main'
        publish(a, pin, [update(name, new=oid(A))])
        if state == 'deleted-ref':
            publish(a, pin, [update(name, old=oid(A))])
    assert abandon(a)['outcome'] == 'not_abandonable'
    assert lifecycle(a) == 'ready'
    assert a.pg.value('SELECT count(*) FROM public.project_deletion_jobs WHERE project_id=' + literal(a.project)) == '0'


def test_rejected_ref_transaction_is_not_published_content(native_empty):
    a = native_empty
    pin = publication(a)
    rejected = json.loads(publish(a, pin, [update(old=oid('b'*40), new=oid(A))]).stdout)
    assert rejected['status'] == 'rejected'
    assert a.pg.value('SELECT count(*) FROM public.version_ref_transactions WHERE project_id=' + literal(a.project)) == '1'
    assert abandon(a)['outcome'] == 'accepted'


def test_push_racing_discard_has_exactly_one_winner(native_empty):
    a = native_empty
    pin = publication(a)
    gate = Barrier(2)

    def cancel():
        gate.wait()
        return abandon(a)

    def commit():
        gate.wait()
        return publish(a, pin, check=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future, push_future = pool.submit(cancel), pool.submit(commit)
        canceled, pushed = cancel_future.result(), push_future.result()
    if canceled['outcome'] == 'accepted':
        assert pushed.returncode and 'repository_unavailable' in pushed.stderr
        assert lifecycle(a) == 'deleting'
    else:
        assert canceled['outcome'] == 'not_abandonable'
        assert pushed.returncode == 0
        assert lifecycle(a) == 'ready'


def test_populated_upgrade_corrects_guard_preserves_acl_and_unknown_resource_protection():
    spec = importlib.util.spec_from_file_location('abandon_upgrade_pg', ROOT / 'scripts/testing/native_postgres.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with Postgres().empty_database() as pg:
        pg.sql((ROOT / 'scripts/testing/postgres_auth_stub.sql').read_text())
        for migration in sorted(EXPAND.parent.glob('*.sql')):
            if migration.name >= EXPAND.name:
                break
            pg.sql(native.product_migration_sql(migration))
        a = create(pg)
        pin = publication(a)
        assert abandon(a)['outcome'] == 'not_abandonable'
        acl_sql = "SELECT jsonb_agg(jsonb_build_array(proname,proacl::text) ORDER BY proname) FROM pg_proc WHERE proname IN ('abandon_project_initialization','_project_initialization_has_cascade_dependents')"
        acl = pg.value(acl_sql)
        before = pg.value('SELECT to_jsonb(p) FROM public.projects p WHERE id=' + literal(a.project))
        sql = EXPAND.read_text()
        assert pg.sql(sql.replace('NOTIFY pgrst', 'SELECT 1/0;\nNOTIFY pgrst'), check=False).returncode
        assert abandon(a)['outcome'] == 'not_abandonable'
        pg.sql(sql)
        assert pg.value(acl_sql) == acl
        assert pg.value('SELECT to_jsonb(p) FROM public.projects p WHERE id=' + literal(a.project)) == before
        pg.sql('CREATE TABLE public.abandon_test_resource(project_id text REFERENCES public.projects(id) ON DELETE CASCADE); INSERT INTO public.abandon_test_resource VALUES(' + literal(a.project) + ')')
        assert abandon(a)['outcome'] == 'not_abandonable'
        pg.sql('DROP TABLE public.abandon_test_resource')
        assert abandon(a)['outcome'] == 'accepted'
        assert publish(a, pin, check=False).returncode
