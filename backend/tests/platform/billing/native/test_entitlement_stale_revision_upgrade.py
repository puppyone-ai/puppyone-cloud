"""The stale-revision Expand on a populated database.

The migration replaces ``publish_organization_entitlement`` to change one
error code. On a database that already holds published entitlements it must
keep every row, the function's owner, grants and settings, roll back whole if
it fails, and afterwards refuse a stale revision with PT409 instead of 40001.
"""

from __future__ import annotations

import importlib.util

import pytest

from tests.platform.workspace.native.conftest import ROOT, NativeDatabase

from .conftest import space

pytestmark = pytest.mark.native

EXPAND = ROOT / "supabase/migrations/20261123010000_expand_entitlement_stale_revision_status.sql"
FUNCTION = (
    "public.publish_organization_entitlement(text,text,text,text,text,jsonb,integer,text,"
    "bigint,timestamptz,timestamptz,timestamptz,text,text,text)"
)
TABLES = ("organization_entitlements", "organization_entitlement_events")


def publish(org: str, revision: int, digit: str) -> str:
    return (
        f"public.publish_organization_entitlement('{org}', '1.0', 'plus', 'active', 'puppypay', "
        f"'{{}}'::jsonb, 2, '2026-07-14', {revision}, now(), NULL, NULL, repeat('{digit}', 64), "
        f"'event-{revision}', 'upgrade-test')"
    )


def outcome(pg: NativeDatabase, call: str) -> str:
    """The publication outcome, or the SQLSTATE that refused it."""

    return pg.sql(
        "CREATE TEMP TABLE IF NOT EXISTS answer(value text); TRUNCATE answer;\n"
        f"DO $$ BEGIN INSERT INTO answer SELECT ({call})->>'outcome';\n"
        "EXCEPTION WHEN OTHERS THEN INSERT INTO answer VALUES (SQLSTATE); END $$;\n"
        "SELECT value FROM answer;"
    )


def rows(pg: NativeDatabase) -> dict[str, str]:
    return {
        table: pg.sql(
            f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM public.{table} t"
        )
        for table in TABLES
    }


def test_the_expand_keeps_data_and_grants_and_ends_the_retry_loop() -> None:
    spec = importlib.util.spec_from_file_location(
        "stale_revision_upgrade_postgres", ROOT / "scripts/testing/native_postgres.py"
    )
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    previous = max(m.name for m in EXPAND.parent.glob("*.sql") if m.name < EXPAND.name)

    with native.native_postgres(through=previous) as cluster:
        pg = NativeDatabase(cluster["url"])
        org = space(pg)["org"]
        assert outcome(pg, publish(org, 1, "a")) == "inserted"
        assert outcome(pg, publish(org, 2, "b")) == "updated"
        before = rows(pg)
        definition = (
            "SELECT proowner::regrole::text, proacl::text, prosecdef, proconfig::text "
            f"FROM pg_proc WHERE oid = '{FUNCTION}'::regprocedure"
        )
        old_definition = pg.sql(definition)
        assert outcome(pg, publish(org, 1, "a")) == "40001"

        sql = native.product_migration_sql(EXPAND)
        assert sql.count("COMMIT;") == 1
        with pytest.raises(RuntimeError, match="division by zero"):
            pg.sql(sql.replace("COMMIT;", "SELECT 1/0;\nCOMMIT;"))
        assert outcome(pg, publish(org, 1, "a")) == "40001", "a failed upgrade changes nothing"
        assert pg.sql(definition) == old_definition

        pg.sql(sql)
        assert rows(pg) == before
        assert pg.sql(definition) == old_definition
        assert outcome(pg, publish(org, 1, "a")) == "PT409"
        assert outcome(pg, publish(org, 2, "b")) == "idempotent"
        assert outcome(pg, publish(org, 2, "c")) == "23505"
        assert outcome(pg, publish(org, 3, "c")) == "updated"
