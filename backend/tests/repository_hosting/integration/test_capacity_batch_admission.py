"""Real PG authorization work stays per transaction, with all fences intact."""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.repository_hosting.harness.postgres import literal
from tests.repository_hosting.harness.transaction import transaction, wait_for_lock
from tests.repository_hosting.integration.test_ingestion_metadata_pages import facts
from tests.repository_hosting.integration.test_repository_capacity import usage
from tests.repository_hosting.integration.test_repository_file_policy import (
    admitted_pin,
)
from tests.repository_hosting.integration.test_repository_file_policy import (
    policy as policy_fixture,
)
from tests.repository_hosting.integration.test_repository_write_admission import (
    admission as admission_fixture,
)
from tests.repository_hosting.integration.test_repository_write_admission import (
    seed_credential,
)

pytestmark = pytest.mark.hosting_live
admission = admission_fixture
policy = policy_fixture


@pytest.fixture
def writer(policy):
    a = policy
    a.surface, a.credential, a.actor = seed_credential(a)
    a.pin = admitted_pin(a)
    return a


def query(a, rows, *, entry="page", io=None):
    name = "reserve_version_object_capacity" + ("_page" if entry == "page" else "")
    return "SELECT public." + name + "(" + ",".join(
        map(literal, (a.project, a.actor, a.pin, rows, True, io))
    ) + ");"


def reserve(a, rows, **kwargs):
    return a.pg.sql("SET ROLE service_role;" + query(a, rows, **kwargs), check=False)


def counts(a):
    return json.loads(a.pg.value(
        "SELECT jsonb_build_array("
        + ",".join(
            f"(SELECT count(*) FROM public.{table} WHERE project_id={literal(a.project)})"
            for table in (
                "version_repository_object_capacity", "version_repository_capacity_inflight",
                "version_repository_capacity_events",
            )
        ) + ")"
    ))


PROFILE_FUNCTIONS = (
    "resolve_git_runtime_credential", "resolve_project_role", "_version_file_policy_limit",
    "_version_begin_capacity_admission", "_version_finish_capacity_admission",
    "_version_assert_write_lease", "_version_guard_object_capacity_page",
)


def measured_reservation(a, rows, *, entry="page"):
    names = ",".join(map(literal, PROFILE_FUNCTIONS))
    a.pg.sql("SELECT pg_stat_reset_single_function_counters(oid) FROM pg_proc "
             f"WHERE pronamespace='public'::regnamespace AND proname IN ({names})")
    # One real RPC, including policy, both bulk inserts, counters, event and FKs.
    # Eight seconds matches the observed hosted statement limit. Call-count
    # assertions are the stable performance contract; timing is supplementary.
    result = a.pg.sql(
        "SET track_functions='all'; SET statement_timeout='8s'; SET ROLE service_role;"
        "EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF) "
        + query(a, rows, entry=entry, io=str(uuid.uuid4()))
        + "RESET ROLE; SELECT pg_stat_force_next_flush();"
    )
    plan, _ = json.JSONDecoder().raw_decode(result.stdout.strip())
    calls = json.loads(a.pg.value(
        "SELECT coalesce(jsonb_object_agg(funcname,calls),'{}') FROM pg_stat_user_functions "
        f"WHERE schemaname='public' AND funcname IN ({names})"
    ))
    return plan[0]["Execution Time"], calls


@pytest.mark.parametrize("count,entry", [(1, "page"), (6017, "page"), (16384, "page"), (200, "legacy")])
def test_authorization_count_is_constant_for_real_runtime_policy(writer, count, entry, record_property):
    a = writer
    elapsed, calls = measured_reservation(a, facts(count), entry=entry)
    assert calls == {
        "resolve_git_runtime_credential": 1, "resolve_project_role": 1,
        "_version_file_policy_limit": 1, "_version_begin_capacity_admission": 1,
        "_version_finish_capacity_admission": 1, "_version_assert_write_lease": 2,
        "_version_guard_object_capacity_page": 1,
    }
    assert usage(a.authority) == [count, count]
    assert counts(a) == [count, count, 1]
    record_property("capacity_execution_ms", elapsed)
    record_property("capacity_objects", count)
    # A fresh I/O attempt must be authorized again even if every object exists.
    _, calls = measured_reservation(a, facts(count), entry=entry)
    assert calls["resolve_git_runtime_credential"] == calls["resolve_project_role"] == 1
    assert calls["_version_guard_object_capacity_page"] == 0
    assert usage(a.authority) == [count, count]
    assert counts(a) == [count, 2 * count, 1]


@pytest.mark.parametrize("entry", ["page", "legacy"])
@pytest.mark.parametrize("existing", [False, True])
def test_revoked_writer_cannot_reserve_even_metadata_only_duplicates(writer, entry, existing):
    a = writer
    if existing:
        assert reserve(a, facts(), io=str(uuid.uuid4())).returncode == 0
    before, before_counts = usage(a.authority), counts(a)
    a.pg.sql(f"UPDATE public.project_members SET role='viewer' WHERE user_id={literal(a.user)}")
    result = reserve(a, facts(2), entry=entry)
    assert result.returncode and "repository_action_denied" in result.stderr
    assert usage(a.authority) == before and counts(a) == before_counts


@pytest.mark.parametrize("entry", ["page", "legacy"])
def test_file_limit_tail_is_atomic_and_existing_objects_are_grandfathered(writer, entry):
    a = writer
    old = [{**facts(1)[0], "body_bytes": 4}]
    assert reserve(a, old, entry=entry).returncode == 0
    a.pg.sql("UPDATE public.organization_entitlements SET entitlements=jsonb_set("
             f"entitlements,'{{limits,upload.max_single_file_bytes}}','2') WHERE org_id={literal(a.org)}")
    rows = [*old, *facts(2, offset=1)]
    assert reserve(a, rows, entry=entry, io=str(uuid.uuid4())).returncode == 0
    before = counts(a)
    invalid = [*facts(3, offset=20), {**facts(1, offset=30)[0], "body_bytes": 3}]
    result = reserve(a, invalid, entry=entry, io=str(uuid.uuid4()))
    assert result.returncode and "file_size_limit_exceeded" in result.stderr
    assert usage(a.authority) == [6, 3] and counts(a) == before


@pytest.mark.parametrize("kind", ["blob", "tree"])
def test_bare_object_budget_is_preserved_for_bulk_insert(writer, kind):
    a = writer
    a.pg.sql("UPDATE public.organization_entitlements SET entitlements=jsonb_set("
             f"entitlements,'{{limits,upload.max_single_file_bytes}}','null') WHERE org_id={literal(a.org)};"
             f"UPDATE public.version_repository_billing SET accounted_bytes=0 WHERE project_id={literal(a.project)}")
    result = reserve(a, [*facts(2), {**facts(1, offset=20)[0], "object_kind": kind,
                                   "body_bytes": 67108865}], io=str(uuid.uuid4()))
    assert result.returncode and "repository_single_object_budget_exceeded" in result.stderr
    assert usage(a.authority) == [0, 0] and counts(a) == [0, 0, 0]


def insert_barrier(a):
    """Wait after admission/INSERT, without relying on scheduler speed."""
    key = int(uuid.uuid4().hex[:12], 16)
    name = "test_capacity_barrier_" + uuid.uuid4().hex
    a.pg.sql(f"""
        CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.project_id={literal(a.project)} THEN PERFORM pg_advisory_xact_lock({key}); END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER {name} AFTER INSERT ON public.version_repository_object_capacity
            FOR EACH ROW EXECUTE FUNCTION public.{name}();
    """)
    return key, name


@pytest.mark.parametrize("expiry", ["credential", "lease", "entitlement", "pin"])
def test_expiry_after_bulk_admission_rolls_back_every_effect(writer, expiry):
    a = writer
    key, trigger = insert_barrier(a)
    table, column, where, error = {
        "credential": ("access_surface_credentials", "expires_at", f"id={literal(a.credential)}", "repository_action_denied"),
        "lease": ("project_write_leases", "expires_at", f"id={literal(a.lease)}", "repository_write_lease_unavailable"),
        "entitlement": ("organization_entitlements", "effective_until", f"org_id={literal(a.org)}", "storage_billing_entitlement_invalid"),
        "pin": ("version_object_pins", "expires_at", f"id={literal(a.pin)}", "publication_pin_unavailable"),
    }[expiry]
    application = "capacity-expiry-" + uuid.uuid4().hex
    try:
        with ThreadPoolExecutor(1) as pool:
            with transaction(a.pg, f"SELECT pg_advisory_xact_lock({key})") as blocker:
                a.pg.sql(f"UPDATE public.{table} SET {column}=clock_timestamp()+interval '2 seconds' WHERE {where}")
                future = pool.submit(a.pg.sql, f"SET application_name={literal(application)};SET ROLE service_role;"
                                     + query(a, facts(2), io=str(uuid.uuid4())), check=False)
                wait_for_lock(a.pg, application)
                deadline = time.monotonic() + 8
                while a.pg.value(f"SELECT {column}<=clock_timestamp() FROM public.{table} WHERE {where}") != "t":
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                blocker.execute("COMMIT")
            result = future.result(timeout=10)
        assert result.returncode and error in result.stderr
        assert usage(a.authority) == [0, 0] and counts(a) == [0, 0, 0]
    finally:
        a.pg.sql(f"DROP TRIGGER {trigger} ON public.version_repository_object_capacity; DROP FUNCTION public.{trigger}()")


@pytest.mark.parametrize("fact", ["credential", "membership"])
def test_batch_admission_serializes_revocation_and_rechecks_next_transaction(writer, fact):
    a = writer
    revoke = (
        f"UPDATE public.access_surface_credentials SET status='revoked' WHERE id={literal(a.credential)}"
        if fact == "credential" else
        f"UPDATE public.project_members SET role='viewer' WHERE user_id={literal(a.user)}"
    )
    name = "capacity-revoke-" + uuid.uuid4().hex
    with ThreadPoolExecutor(1) as pool:
        with transaction(a.pg, "SET ROLE service_role;" + query(a, facts(2))) as session:
            pending = pool.submit(a.pg.sql, f"SET application_name={literal(name)};" + revoke)
            wait_for_lock(a.pg, name)
            assert not pending.done()
            session.execute("COMMIT")
        assert pending.result(timeout=10).returncode == 0
    result = reserve(a, facts(2), io=str(uuid.uuid4()))
    assert result.returncode and "repository_action_denied" in result.stderr
    assert usage(a.authority) == [2, 2] and counts(a) == [2, 0, 1]


def test_capacity_tables_and_private_context_have_no_application_write_bypass(writer):
    a = writer
    for signature in (
        "_version_begin_capacity_admission(text,text,uuid)",
        "_version_finish_capacity_admission(text,jsonb)",
        "_version_guard_object_capacity_page()",
    ):
        for role in ("anon", "authenticated", "service_role"):
            assert a.pg.value(f"SELECT has_function_privilege({literal(role)},{literal('public.' + signature)},'EXECUTE')") == "f"
    for table in ("version_repository_object_capacity", "version_repository_capacity_inflight"):
        for role in ("anon", "authenticated", "service_role"):
            assert a.pg.value(f"SELECT has_table_privilege({literal(role)},{literal('public.' + table)},'INSERT,UPDATE,DELETE')") == "f"
    # Any future SECURITY DEFINER writer must be reviewed as part of this boundary.
    writers = json.loads(a.pg.value("""
        SELECT json_agg(proname ORDER BY proname) FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND prosrc ~* 'INSERT INTO public.version_repository_(object_capacity|capacity_inflight)'
    """))
    assert writers == sorted([
        "reserve_version_object_capacity_page", "finish_repository_recovery_archive",
        "activate_native_repository_migrations", "activate_prepared_native_repository_projects",
    ])
    for signature in (
        "finish_repository_recovery_archive(text)", "activate_native_repository_migrations(text)",
        "activate_prepared_native_repository_projects(text,text[],uuid)",
    ):
        assert a.pg.value(f"SELECT has_function_privilege('service_role',{literal('public.' + signature)},'EXECUTE')") == "f"


def test_owner_archive_keeps_verified_inventory_exception_and_runtime_is_denied(writer):
    a = writer
    # Owner-installed SQL fixture, not S3 verification. Historical bytes may
    # exceed today's per-file policy without becoming published Git roots.
    a.pg.sql(f"""
        INSERT INTO public.version_repository_archives(project_id,source)
            VALUES({literal(a.project)},'{{}}');
        INSERT INTO public.version_repository_archive_git_objects
            (project_id,source_id,source_kind,object_id,object_kind,body_bytes,source_sha256)
            VALUES({literal(a.project)},'old-object','blob',{literal('b' * 40)},'blob',16,{literal('f' * 64)});
    """)
    finish = f"SELECT public.finish_repository_recovery_archive({literal(a.project)})"
    denied = a.pg.sql(finish, check=False)
    assert denied.returncode and "verified_archive_required" in denied.stderr
    assert usage(a.authority) == [0, 0]
    a.pg.sql(f"UPDATE public.version_repository_archives SET state='verified',manifest_sha256={literal('f' * 64)} "
             f"WHERE project_id={literal(a.project)}")
    denied = a.pg.sql("SET ROLE service_role;" + finish, check=False)
    assert denied.returncode and "permission denied" in denied.stderr
    a.pg.sql(finish)
    a.pg.sql(finish)
    assert usage(a.authority) == [16, 1] and counts(a) == [1, 0, 0]
    assert a.authority.count("version_ref_transactions") == 0
