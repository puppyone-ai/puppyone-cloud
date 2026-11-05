"""Release upgrade rehearsal for the device-host migrations on real PostgreSQL 17.

The database is built exactly as the accepted release (origin/qubits) leaves
it, filled with data, and only then upgraded with this branch's host
migrations. They are additive: nothing that existed changes, and the new
objects work and stay service-only.
"""

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAST_ACCEPTED = "20261105090000_workspace_state_exits.sql"
HOST_MIGRATIONS = ["20261105120000_expand_device_hosts.sql", "20261106100000_expand_host_execution.sql",
                   "20261107090000_expand_host_device_challenge.sql", "20261107100000_expand_host_tunnels.sql"]
OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
STRANGER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def native():
    spec = importlib.util.spec_from_file_location("host_upgrade_postgres", ROOT / "scripts/testing/native_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def psql(url, statement):
    result = subprocess.run(["psql", url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"], input=statement,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


SNAPSHOT = """
SELECT string_agg(format('%s.%s=%s', table_schema, table_name,
  (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
   false, true, '')))[1]::text), ',' ORDER BY table_schema, table_name)
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema IN ('public', 'auth') AND table_name NOT LIKE 'host\\_%';
"""


def test_host_migrations_upgrade_the_accepted_release_without_touching_existing_data():
    module = native()
    current = sorted(path.name for path in (ROOT / "supabase/migrations").glob("*.sql"))
    # This branch adds exactly the host migrations after the accepted release.
    assert current[current.index(LAST_ACCEPTED) + 1:] == HOST_MIGRATIONS
    with module.native_postgres(through=LAST_ACCEPTED) as cluster:
        url = cluster["url"]
        assert cluster["migrations"][-1] == LAST_ACCEPTED
        psql(url, f"INSERT INTO auth.users(id) VALUES ('{OWNER}'), ('{STRANGER}');")
        before_schema = psql(url, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                  "WHERE n.nspname='public' AND p.proname NOT LIKE 'host\\_%';")
        before_rows = psql(url, SNAPSHOT)
        assert psql(url, "SELECT to_regclass('public.host_devices') IS NULL;") == "t"

        for name in HOST_MIGRATIONS:
            psql(url, module.product_migration_sql(ROOT / "supabase/migrations" / name))

        # Additive: no existing table's rows and no existing function changed.
        assert psql(url, SNAPSHOT) == before_rows
        assert psql(url, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                         "WHERE n.nspname='public' AND p.proname NOT LIKE 'host\\_%';") == before_schema
        # Every host RPC is callable by the service role only.
        privileges = psql(url, """
          SELECT string_agg(DISTINCT format('%s:%s%s%s', p.proname,
            has_function_privilege('anon', p.oid, 'EXECUTE')::int,
            has_function_privilege('authenticated', p.oid, 'EXECUTE')::int,
            has_function_privilege('service_role', p.oid, 'EXECUTE')::int), ',')
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname LIKE 'host\\_%' AND p.proname <> 'host_capability_for';""")
        assert privileges and all(item.endswith(":001") for item in privileges.split(",")), privileges
        assert "host_device_key:001" in privileges and "host_file_event_record:001" in privileges

        # The upgraded schema works: register, read the key back, record a tunnel.
        device = psql(url, f"SET ROLE service_role; SELECT public.host_device_register('{OWNER}', "
                           "decode(repeat('ab',32),'hex'), 'Mac', 'darwin');").splitlines()[-1]
        key = psql(url, f"SET ROLE service_role; SELECT public.host_device_key('{OWNER}', '{device}')->>'public_key';")
        assert key.splitlines()[-1] == "ab" * 32
        stranger = subprocess.run(["psql", url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"], capture_output=True, text=True,
                                  input=f"SET ROLE service_role; SELECT public.host_device_key('{STRANGER}', '{device}');")
        assert stranger.returncode != 0 and "host_access_denied" in stranger.stderr
        folder = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        psql(url, f"SET ROLE service_role; SELECT public.host_folder_register('{OWNER}', '{device}', '{folder}', "
                  "'Notes', ARRAY['folder.read']);")
        psql(url, f"SET ROLE service_role; SELECT public.host_file_event_record('{OWNER}', '{device}', '{folder}', "
                  "'tunnel', 'intended');")
        assert psql(url, "SELECT operation || ':' || outcome FROM public.host_file_events;") == "tunnel:intended"
