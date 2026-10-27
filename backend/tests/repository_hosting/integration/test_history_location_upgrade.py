"""Populated additive RPC upgrade on an owned PostgreSQL 17 database."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.agent.runtime import conftest as owned

postgres = owned.postgres
submitted = owned.submitted
pytestmark = pytest.mark.integration


def test_populated_location_paging_and_pin_guards(postgres, submitted):
    project = submitted[0]["project"]
    pin = str(uuid4())
    postgres.sql(f"""
      DROP FUNCTION get_version_pinned_object_locations_page(text,text,uuid,text,integer);
      INSERT INTO version_repositories(project_id,authority) VALUES('{project}','native');
      INSERT INTO version_object_pins(id,project_id,actor,object_format,generation,gc_epoch,purpose,roots,expires_at)
      VALUES('{pin}','{project}','reader','sha1',1,1,'read','{{}}',now()+interval '5 min');
      INSERT INTO version_object_locations(project_id,object_id,pack_key,offset_bytes,size_bytes)
      SELECT '{project}',lpad(to_hex(i),40,'0'),'version/{project}/object-bundles/pack.pack',i,1
      FROM generate_series(1,50001) i;
    """)
    migration = (
        Path(__file__).resolve().parents[4]
        / "supabase/migrations/20261023050000_page_pinned_object_locations.sql"
    )
    postgres.sql(migration.read_text())
    cursor, seen, requests = "", set(), 0
    while True:
        page = json.loads(
            postgres.sql(
                f"SELECT get_version_pinned_object_locations_page('{project}','reader','{pin}','{cursor}',4096)"
            )
        )
        requests += 1
        assert len(page["rows"]) <= 4096
        ids = [row["object_id"] for row in page["rows"]]
        assert not seen.intersection(ids)
        seen.update(ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 50001 and requests == 13
    assert (
        postgres.sql(f"SELECT count(*) FROM version_object_locations WHERE project_id='{project}'")
        == "50001"
    )
    call = f"SELECT get_version_pinned_object_locations_page('{project}','reader','{pin}')"
    for role in ("anon", "authenticated"):
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(f"SET ROLE {role}; {call}")
    with pytest.raises(RuntimeError, match="repository_read_pin_unavailable"):
        postgres.sql(call.replace("'reader'", "'someone-else'"))
    with pytest.raises(RuntimeError, match="invalid_object_location_page"):
        postgres.sql(
            f"SELECT get_version_pinned_object_locations_page('{project}','reader','{pin}','',4097)"
        )
    postgres.sql(
        f"UPDATE version_object_pins SET roots='{{\"{'a' * 40}\":\"commit\"}}' WHERE id='{pin}'"
    )
    for column, value in [
        ("generation", "2"),
        ("gc_epoch", "2"),
        ("purpose", "'publication'"),
        ("state", "'released'"),
        ("expires_at", "now()-interval '1 second'"),
    ]:
        with pytest.raises(RuntimeError, match="repository_read_pin_unavailable"):
            postgres.sql(
                f"BEGIN; UPDATE version_object_pins SET {column}={value} WHERE id='{pin}'; {call}; ROLLBACK;"
            )
