"""Populated standard-pack location expansion using isolated real PG."""

import importlib.util
import json
from pathlib import Path

import pytest

from tests.repository_hosting.harness.postgres import Postgres, literal
from tests.repository_hosting.harness.ref_authority import Authority
from tests.repository_hosting.integration.test_object_location_fence import location
from tests.repository_hosting.integration.test_publication_pins import begin, rpc
from tests.repository_hosting.integration.test_publication_pins_upgrade import rows

pytestmark = pytest.mark.hosting_live
ROOT = Path(__file__).resolve().parents[4]
EXPAND = ROOT / "supabase/migrations/20261022030000_expand_standard_git_pack_locations.sql"
FUNCTIONS = ("register_version_object_locations", "register_version_object_location_page")


def test_populated_pack_expansion_preserves_data_acl_and_fencing():
    spec = importlib.util.spec_from_file_location(
        "pack_upgrade_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with Postgres().empty_database() as pg:
        pg.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text())
        for migration in sorted(EXPAND.parent.glob("*.sql")):
            if migration.name >= EXPAND.name:
                break
            pg.sql(native.product_migration_sql(migration))
        project = pg.create_project()
        auth = Authority(pg, project)
        pin, _ = begin(auth)
        old = location(project)
        rpc(pg, FUNCTIONS[0], project, "test:writer", pin, [old])
        before = rows(pg, "version_object_locations")
        acl_query = "SELECT jsonb_agg(jsonb_build_array(proname,proacl::text) ORDER BY proname) FROM pg_proc WHERE proname IN ('register_version_object_locations','register_version_object_location_page')"
        acl = pg.value(acl_query)
        new = dict(
            old,
            object_id="b" * 40,
            pack_key=old["pack_key"].replace(".pob", ".pack"),
            offset_bytes=12,
        )
        for function in FUNCTIONS:
            result = rpc(pg, function, project, "test:writer", pin, [new], check=False)
            assert result.returncode and "noncanonical_object_location" in result.stderr
        sql = EXPAND.read_text()
        failed = pg.sql(sql.replace("NOTIFY pgrst", "SELECT 1/0;\nNOTIFY pgrst"), check=False)
        assert failed.returncode and "division by zero" in failed.stderr
        assert rows(pg, "version_object_locations") == before
        assert pg.value(acl_query) == acl
        pg.sql(sql)
        assert rows(pg, "version_object_locations") == before
        assert pg.value(acl_query) == acl
        for function in FUNCTIONS:
            rpc(pg, function, project, "test:writer", pin, [old, new])
            invalid = dict(new, pack_key=new["pack_key"].replace(project, "another-project"))
            denied = rpc(pg, function, project, "test:writer", pin, [invalid], check=False)
            assert denied.returncode and "noncanonical_object_location" in denied.stderr
        pg.sql(
            f"UPDATE public.version_object_pins SET expires_at=clock_timestamp()-interval '1 second' WHERE id={literal(pin)}"
        )
        for function in FUNCTIONS:
            denied = rpc(pg, function, project, "test:writer", pin, [new], check=False)
            assert denied.returncode and "publication_pin_unavailable" in denied.stderr
        assert len(json.loads(rows(pg, "version_object_locations"))) == 2
