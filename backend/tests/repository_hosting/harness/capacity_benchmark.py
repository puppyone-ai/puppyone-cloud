"""Compare complete capacity RPCs before/after migration in owned local PG17.

Run from backend: python -m tests.repository_hosting.harness.capacity_benchmark
  --pack /path/to/complete.pack --output /tmp/capacity.json

No developer credentials or existing database URL are accepted. Neither S3 nor
final ref publication is measured. Each measurement rolls back its own writes.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path

from src.version_engine.storage.git_pack import GitPack
from tests.repository_hosting.harness.postgres import Postgres, literal
from tests.repository_hosting.integration.test_capacity_batch_admission import (
    PROFILE_FUNCTIONS,
    query,
)
from tests.repository_hosting.integration.test_repository_file_policy import admitted_pin, policy
from tests.repository_hosting.integration.test_repository_write_admission import (
    admission,
    seed_credential,
)

ROOT = Path(__file__).resolve().parents[4]
MIGRATION = ROOT / "supabase/migrations/20261023000000_batch_capacity_authorization.sql"


def measure(a, objects):
    tracked = (*PROFILE_FUNCTIONS, "_version_assert_current_repository_actor",
               "_version_fence_new_blob_size", "_version_fence_native_io_admission")
    names = ",".join(map(literal, tracked))
    a.pg.sql("SELECT pg_stat_reset_single_function_counters(oid) FROM pg_proc "
             f"WHERE pronamespace='public'::regnamespace AND proname IN ({names})")
    start = time.perf_counter()
    result = a.pg.sql("BEGIN; SET LOCAL track_functions='all'; SET LOCAL statement_timeout='35s'; "
                      "SET LOCAL ROLE service_role; EXPLAIN (ANALYZE,FORMAT JSON,TIMING OFF) "
                      + query(a, objects, io=str(uuid.uuid4()))
                      + "ROLLBACK; SELECT pg_stat_force_next_flush();")
    wall = time.perf_counter() - start
    plan, _ = json.JSONDecoder().raw_decode(result.stdout.strip())
    functions = json.loads(a.pg.value(
        "SELECT json_agg(x ORDER BY x.funcname) FROM (SELECT funcname,calls,total_time,self_time "
        f"FROM pg_stat_user_functions WHERE schemaname='public' AND funcname IN ({names})) x"
    ))
    return {"execution_ms": plan[0]["Execution Time"], "wall_seconds": round(wall, 4),
            "functions": functions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(APP_ENV="test", SUPABASE_URL="http://127.0.0.1:1",
                      SUPABASE_KEY="isolated-capacity-benchmark", MANAGED_AI_ENABLED="false")
    spec = importlib.util.spec_from_file_location("capacity_benchmark_pg", ROOT / "scripts/testing/native_postgres.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with args.pack.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with GitPack("sha1") as pack:
        with args.pack.open("rb") as source:
            pack.read(source)
        objects = [dict(object_id=oid, object_kind=value[0], body_bytes=value[2])
                   for oid, value in pack.objects.items()]
    report = dict(scope="Local PG17, actual full capacity RPC with Runtime credential/file policy; no S3/network/publication",
                  pack_sha256=digest, pack_bytes=args.pack.stat().st_size, objects=len(objects),
                  logical_bytes=sum(obj["body_bytes"] for obj in objects))
    with native.native_postgres() as info:
        os.environ.update(HOSTING_TEST_DB_URL=info["url"], HOSTING_TEST_STACK=info["stack"])
        with Postgres().empty_database() as pg:
            pg.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text())
            for migration in sorted(MIGRATION.parent.glob("*.sql")):
                if migration.name >= MIGRATION.name:
                    break
                pg.sql(native.product_migration_sql(migration))
            a = policy.__wrapped__(admission.__wrapped__((pg, pg.create_project())))
            a.surface, a.credential, a.actor = seed_credential(a)
            pg.sql("UPDATE public.organization_entitlements SET entitlements=jsonb_set("
                   f"entitlements,'{{limits,upload.max_single_file_bytes}}','67108864') WHERE org_id={literal(a.org)};"
                   f"UPDATE public.project_write_leases SET expires_at=clock_timestamp()+interval '10 minutes' WHERE id={literal(a.lease)}")
            a.pin = admitted_pin(a)
            pg.sql(f"UPDATE public.version_object_pins SET expires_at=clock_timestamp()+interval '10 minutes' WHERE id={literal(a.pin)}")
            report["before"] = measure(a, objects)
            print("before_ms", report["before"]["execution_ms"], flush=True)
            pg.sql(MIGRATION.read_text())
            report["after"] = [measure(a, objects) for _ in range(3)]
            print("after_ms", [run["execution_ms"] for run in report["after"]], flush=True)
            assert pg.value(f"SELECT count(*) FROM public.version_repository_object_capacity WHERE project_id={literal(a.project)}") == "0"
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
