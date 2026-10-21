"""Real PostgreSQL page atomicity, fences and compatibility; no S3 latency claim."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.repository_hosting.harness.postgres import literal
from tests.repository_hosting.integration.test_publication_pins import begin, rpc
from tests.repository_hosting.integration.test_repository_capacity import setup, usage

pytestmark = pytest.mark.hosting_live


def prepared(pg_project, limit=1000):
    auth, pin, root = setup(pg_project)
    auth.pg.sql(
        f"UPDATE public.version_repository_capacity SET max_objects={limit},max_body_bytes={limit} "
        f"WHERE project_id={literal(auth.project)}; "
        f"UPDATE public.version_organization_capacity SET max_objects={limit},max_body_bytes={limit} "
        f"WHERE org_id=(SELECT org_id FROM public.projects WHERE id={literal(auth.project)});"
    )
    return auth, pin, root


def facts(count=401, offset=0):
    return [
        dict(object_id=f"{n + 1 + offset:040x}", object_kind="blob", body_bytes=1)
        for n in range(count)
    ]


def reserve(auth, pin, rows, io_id=None, actor="test:writer", check=True):
    return rpc(
        auth.pg,
        "reserve_version_object_capacity_page",
        auth.project,
        actor,
        pin,
        rows,
        True,
        io_id,
        check=check,
    )


def locations(auth, rows):
    return [
        dict(
            project_id=auth.project,
            object_id=row["object_id"],
            offset_bytes=n,
            size_bytes=1,
            pack_key=f"version/{auth.project}/object-bundles/aa/{'a' * 64}.pob",
        )
        for n, row in enumerate(rows)
    ]


def count(auth, table):
    return int(
        auth.pg.value(
            f"SELECT count(*) FROM public.{table} WHERE project_id={literal(auth.project)}"
        )
    )


def test_page_capacity_is_idempotent_and_settlement_cannot_release_other_operation(pg_project):
    auth, pin, _ = prepared(pg_project)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    rows = facts()
    assert json.loads(reserve(auth, pin, rows, first).stdout) == {
        "new_objects": 401,
        "new_body_bytes": 401,
    }
    assert json.loads(reserve(auth, pin, rows, second).stdout) == {
        "new_objects": 0,
        "new_body_bytes": 0,
    }
    assert usage(auth) == [401, 401]
    assert count(auth, "version_repository_capacity_inflight") == 802
    rpc(auth.pg, "settle_version_object_capacity_io", auth.project, "test:writer", pin, first)
    assert count(auth, "version_repository_capacity_inflight") == 401
    assert usage(auth) == [401, 401]  # settlement does not refund physical capacity


def test_set_page_records_one_admission_event_and_rejects_changed_identity(pg_project):
    auth, pin, _ = prepared(pg_project, limit=7000)
    rows = facts(6014)
    reserve(auth, pin, rows, str(uuid.uuid4()))
    assert usage(auth) == [6014, 6014]
    assert count(auth, "version_repository_capacity_events") == 1
    changed = [{**rows[-1], "body_bytes": 2}, *facts(1, offset=9000)]
    result = reserve(auth, pin, changed, str(uuid.uuid4()), check=False)
    assert result.returncode and "repository_object_identity_mismatch" in result.stderr
    assert usage(auth) == [6014, 6014]
    assert count(auth, "version_repository_capacity_events") == 1


@pytest.mark.parametrize(
    "failure", ["quota", "invalid-tail", "duplicate-across-chunks", "actor", "expired"]
)
def test_whole_page_rolls_back_on_tail_failure_or_stale_owner(pg_project, failure):
    auth, pin, _ = prepared(pg_project, limit=400 if failure == "quota" else 1000)
    rows = facts()
    if failure == "invalid-tail":
        rows[-1]["body_bytes"] = -1
    if failure == "duplicate-across-chunks":
        rows[-1] = rows[0]
    if failure == "expired":
        auth.pg.sql(
            f"UPDATE public.version_object_pins SET expires_at=clock_timestamp()-interval '1 second' WHERE id={literal(pin)}"
        )
    result = reserve(
        auth,
        pin,
        rows,
        str(uuid.uuid4()),
        actor="wrong" if failure == "actor" else "test:writer",
        check=False,
    )
    assert result.returncode
    assert usage(auth) == [0, 0]
    assert count(auth, "version_repository_capacity_inflight") == 0
    assert count(auth, "version_repository_object_capacity") == 0


def test_pages_compete_for_one_quota_without_overspending(pg_project):
    auth, pin, root = prepared(pg_project, limit=600)
    second_pin, _ = begin(auth, roots={root: "commit"})
    barrier = Barrier(2)

    def write(pin, offset):
        barrier.wait(timeout=10)
        return reserve(auth, pin, facts(offset=offset), str(uuid.uuid4()), check=False)

    with ThreadPoolExecutor(2) as pool:
        pending = [pool.submit(write, pin, 0), pool.submit(write, second_pin, 500)]
        results = [future.result(timeout=30) for future in pending]
    assert sum(result.returncode == 0 for result in results) == 1
    assert usage(auth) == [401, 401]


def test_location_page_validates_last_chunk_and_keeps_old_rpc_compatible(pg_project):
    auth, pin, _ = prepared(pg_project)
    rows = locations(auth, facts())
    invalid = [*rows[:-1], {**rows[-1], "pack_key": "another-project/stolen"}]
    result = rpc(
        auth.pg,
        "register_version_object_location_page",
        auth.project,
        "test:writer",
        pin,
        invalid,
        check=False,
    )
    assert result.returncode and "noncanonical_object_location" in result.stderr
    assert count(auth, "version_object_locations") == 0
    result = rpc(
        auth.pg, "register_version_object_location_page", auth.project, "test:writer", pin, rows
    )
    assert json.loads(result.stdout) == {"registered": 401}
    assert count(auth, "version_object_locations") == 401
    assert (
        rpc(
            auth.pg, "register_version_object_locations", auth.project, "test:writer", pin, rows[:1]
        ).returncode
        == 0
    )
    assert rpc(
        auth.pg,
        "register_version_object_locations",
        auth.project,
        "test:writer",
        pin,
        rows,
        check=False,
    ).returncode


def test_proof_page_preserves_dependency_order_and_rolls_back_missing_tail(pg_project):
    auth, pin, _ = prepared(pg_project)
    rows = facts()
    reserve(auth, pin, rows)
    proofs = [
        dict(
            object_id=row["object_id"],
            kind="blob",
            body_bytes=1,
            body_sha256="f" * 64,
            edges=[],
            logical_bytes=1,
            max_blob_bytes=1,
        )
        for row in rows
    ]
    # A dependency that is absent in the final internal chunk must undo earlier chunks.
    bad = [*proofs[:-1], {**proofs[-1], "edges": [["f" * 40, "blob"]]}]
    result = rpc(
        auth.pg,
        "register_version_object_proof_page",
        auth.project,
        "test:writer",
        pin,
        bad,
        check=False,
    )
    assert result.returncode and "dependency_missing" in result.stderr
    assert count(auth, "version_object_proofs") == 0
    proofs[-1]["edges"] = [[proofs[0]["object_id"], "blob"]]
    result = rpc(
        auth.pg, "register_version_object_proof_page", auth.project, "test:writer", pin, proofs
    )
    assert int(result.stdout) == 401


@pytest.mark.parametrize(
    "rows", [None, {}, [], facts(16385), [{"object_id": "a" * 40, "large": "x" * 4194304}]]
)
def test_sql_byte_and_row_bounds_cannot_be_bypassed(pg_project, rows):
    auth, pin, _ = prepared(pg_project)
    result = reserve(auth, pin, rows, check=False)
    assert result.returncode and "ingestion_metadata_page_invalid" in result.stderr
    assert usage(auth) == [0, 0]


def test_page_functions_are_service_role_only(pg_project):
    pg, _ = pg_project
    for signature in (
        "_version_metadata_chunks(jsonb)",
        "reserve_version_object_capacity_page(text,text,uuid,jsonb,boolean,uuid)",
        "register_version_object_location_page(text,text,uuid,jsonb)",
        "register_version_object_proof_page(text,text,uuid,jsonb)",
        "seal_version_capacity_page(text,text,uuid,jsonb,text,jsonb)",
    ):
        for role, expected in (("anon", "f"), ("authenticated", "f"), ("service_role", "t")):
            assert (
                pg.value(f"SELECT has_function_privilege('{role}','public.{signature}','EXECUTE')")
                == expected
            )


def test_seal_page_keeps_unsettled_io_fence_and_atomic_capacity_reconciliation(pg_project):
    auth, pin, _ = prepared(pg_project)
    io_id = str(uuid.uuid4())
    rows = facts()
    reserve(auth, pin, rows[:-1], io_id)
    denied = rpc(
        auth.pg,
        "seal_version_capacity_page",
        auth.project,
        "test:writer",
        pin,
        rows,
        "d" * 64,
        None,
        check=False,
    )
    assert denied.returncode and "repository_capacity_io_unsettled" in denied.stderr
    assert usage(auth) == [400, 400]  # final fact was rolled back with the failed seal
    rpc(auth.pg, "settle_version_object_capacity_io", auth.project, "test:writer", pin, io_id)
    receipt = json.loads(
        rpc(
            auth.pg,
            "seal_version_capacity_page",
            auth.project,
            "test:writer",
            pin,
            rows,
            "d" * 64,
            None,
        ).stdout
    )
    assert receipt["id"] == pin and receipt["manifest_sha256"] == "d" * 64
    assert usage(auth) == [401, 401]
