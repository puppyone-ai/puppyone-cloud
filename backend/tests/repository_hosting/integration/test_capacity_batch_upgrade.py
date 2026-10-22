"""Populated SQL upgrade preserves rows/ACLs and rolls back trigger replacement."""

import importlib.util
import json
from pathlib import Path

import pytest

from tests.repository_hosting.harness.postgres import Postgres, literal
from tests.repository_hosting.integration.test_capacity_batch_admission import counts, reserve
from tests.repository_hosting.integration.test_ingestion_metadata_pages import facts
from tests.repository_hosting.integration.test_publication_pins_upgrade import rows
from tests.repository_hosting.integration.test_repository_file_policy import admitted_pin, policy
from tests.repository_hosting.integration.test_repository_write_admission import (
    admission,
    seed_credential,
)

pytestmark = pytest.mark.hosting_live
ROOT = Path(__file__).resolve().parents[4]
MIGRATION = ROOT / "supabase/migrations/20261023000000_batch_capacity_authorization.sql"


def schema_state(pg):
    return {
        "functions": pg.value("SELECT jsonb_agg(jsonb_build_array(oid,prosrc,proacl,proconfig) ORDER BY oid) "
                              "FROM pg_proc WHERE pronamespace='public'::regnamespace"),
        "triggers": pg.value("SELECT jsonb_agg(jsonb_build_array(tgrelid::regclass::text,tgname,pg_get_triggerdef(oid)) "
                             "ORDER BY tgrelid,tgname) FROM pg_trigger WHERE NOT tgisinternal "
                             "AND tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace)"),
        "table_acl": pg.value("SELECT jsonb_agg(jsonb_build_array(relname,relacl,relrowsecurity) ORDER BY relname) "
                              "FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r'"),
    }


def test_populated_capacity_batch_upgrade_rolls_back_and_preserves_old_callers():
    spec = importlib.util.spec_from_file_location("capacity_upgrade_pg", ROOT / "scripts/testing/native_postgres.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with Postgres().empty_database() as pg:
        pg.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text())
        for migration in sorted(MIGRATION.parent.glob("*.sql")):
            if migration.name >= MIGRATION.name:
                break
            pg.sql(native.product_migration_sql(migration))
        a = policy.__wrapped__(admission.__wrapped__((pg, pg.create_project())))
        a.surface, a.credential, a.actor = seed_credential(a)
        a.pin = admitted_pin(a)
        assert reserve(a, facts(3), entry="legacy").returncode == 0
        tables = (
            "version_repository_object_capacity", "version_repository_capacity_inflight",
            "version_repository_capacity_events", "version_repository_capacity",
            "version_organization_capacity", "version_object_pins", "version_publication_admissions",
            "version_repository_refs", "version_ref_transactions",
        )
        before = {table: rows(pg, table) for table in tables}
        schema = schema_state(pg)
        sql = MIGRATION.read_text()
        failed = pg.sql(sql.replace("NOTIFY pgrst", "SELECT 1/0;\nNOTIFY pgrst"), check=False)
        assert failed.returncode and "division by zero" in failed.stderr
        assert schema_state(pg) == schema
        assert {table: rows(pg, table) for table in tables} == before
        pg.sql(sql)
        assert {table: rows(pg, table) for table in tables} == before
        assert schema_state(pg)["table_acl"] == schema["table_acl"]
        # Existing functions preserve OIDs/grants. New helpers are private.
        previous = {row[0]: row[2] for row in json.loads(schema["functions"])}
        current = {row[0]: row[2] for row in json.loads(schema_state(pg)["functions"])}
        assert {oid: current[oid] for oid in previous} == previous
        assert reserve(a, facts(3), entry="legacy").returncode == 0
        assert counts(a) == [3, 0, 1]
        # Omitting the legacy I/O argument still creates an independent claim.
        pg.sql("SET ROLE service_role; SELECT public.reserve_version_object_capacity("
               + ",".join(map(literal, (a.project, a.actor, a.pin, facts(3)))) + ")")
        assert counts(a) == [3, 3, 1]
        pg.sql(f"UPDATE public.access_surface_credentials SET status='revoked' WHERE id={literal(a.credential)}")
        for entry in ("legacy", "page"):
            denied = reserve(a, facts(1, offset=50), entry=entry)
            assert denied.returncode and "repository_action_denied" in denied.stderr
        assert counts(a) == [3, 3, 1]
