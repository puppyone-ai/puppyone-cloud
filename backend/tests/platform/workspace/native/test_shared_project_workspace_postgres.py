"""Real PostgreSQL 17 tests for one shared default workspace per project."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from .conftest import NativeDatabase, make_running_workbench, seed_project

ROOT = Path(__file__).resolve().parents[5]
MIGRATION = ROOT / "supabase/migrations/20261121020000_expand_shared_project_workspace.sql"


def test_cloud_agent_contracts_passes_after_shared_workspace_migration(pg):
    contract_sql = (ROOT / "supabase/tests/_support/cloud_agent_contracts.inc").read_text(
        encoding="utf-8"
    )
    pg.sql(contract_sql)


def test_two_members_receive_same_default_workspace_and_target_ref(pg):
    env = seed_project(
        pg,
        extra_members=[("second_admin", "admin"), ("editor", "editor"), ("viewer", "viewer")],
    )
    owner_ctx = pg.wb_rpc("context", user=env["owner"], project=env["project"])
    owner_wb = pg.wb_rpc(
        "default", user=env["owner"], project=env["project"], revision=owner_ctx["revision"]
    )
    assert owner_wb["is_default"] is True
    assert owner_wb["internet_access"] is True
    assert owner_wb["target_ref"] == "refs/heads/workspace"

    for member in ("second_admin", "editor", "viewer"):
        member_ctx = pg.wb_rpc("context", user=env[member], project=env["project"])
        assert member_ctx["workbench"]["id"] == owner_wb["id"]
        assert member_ctx["workbench"]["target_ref"] == "refs/heads/workspace"
        same = pg.wb_rpc(
            "default", user=env[member], project=env["project"], revision=member_ctx["revision"]
        )
        assert same["id"] == owner_wb["id"]

    count = pg.row(
        f"SELECT count(*) AS n FROM cloud_workbenches WHERE project_id='{env['project']}' "
        "AND is_default AND owner_deleted_at IS NULL AND agent_registration_id IS NULL"
    )["n"]
    assert count == 1

    # Direct insert of a second active default workbench for the same project fails uniqueness.
    with pytest.raises(RuntimeError, match="cloud_workbenches_project_default"):
        pg.sql(
            "INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,is_default) "
            f"VALUES ('{env['editor']}','{env['project']}','Duplicate','refs/heads/workspace',true);"
        )


def test_shared_workspace_operations_admit_members_and_reject_strangers(pg):
    env = seed_project(
        pg,
        extra_members=[("second_admin", "admin"), ("editor", "editor"), ("viewer", "viewer")],
    )
    stranger = str(uuid4())
    pg.sql(f"INSERT INTO auth.users(id) VALUES ('{stranger}');")

    wb = make_running_workbench(pg, env["owner"], env["project"])
    ctx = pg.wb_rpc("context", user=env["editor"], project=env["project"])

    # Editor can record read/write activity, update sandbox settings, and acquire SSH connections.
    updated = pg.wb_rpc(
        "activity",
        workbench=wb["id"],
        generation=wb["generation"],
        user=env["editor"],
        project=env["project"],
        revision=ctx["revision"],
        write=True,
    )
    assert updated["id"] == wb["id"]
    assert updated["unpublished_changes"] is True

    settings_row = pg.wb_rpc(
        "set_sandbox_settings",
        workbench=wb["id"],
        user=env["editor"],
        project=env["project"],
        settings={"desktop": True, "screen_stream": False},
        revision=ctx["revision"],
    )
    assert settings_row["sandbox_settings"] == {"desktop": True, "screen_stream": False}

    conn_id = str(uuid4())
    conn = pg.wb_rpc(
        "connection_acquire",
        id=conn_id,
        workbench=wb["id"],
        generation=wb["generation"],
        user=env["editor"],
        project=env["project"],
        revision=ctx["revision"],
    )
    assert conn["workbench_id"] == wb["id"]
    assert conn["user_id"] == env["editor"]
    pg.wb_rpc("connection_update", id=conn_id, generation=wb["generation"], action="stop")
    pg.wb_rpc("connection_update", id=conn_id, generation=wb["generation"], action="release")

    # Viewer can read activity, but cannot write, change settings, open, or acquire SSH shell.
    viewer_ctx = pg.wb_rpc("context", user=env["viewer"], project=env["project"])
    read_act = pg.wb_rpc(
        "activity",
        workbench=wb["id"],
        generation=wb["generation"],
        user=env["viewer"],
        project=env["project"],
        revision=viewer_ctx["revision"],
        write=False,
    )
    assert read_act["id"] == wb["id"]
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        pg.wb_rpc(
            "activity",
            workbench=wb["id"],
            generation=wb["generation"],
            user=env["viewer"],
            project=env["project"],
            revision=viewer_ctx["revision"],
            write=True,
        )
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        pg.wb_rpc(
            "set_sandbox_settings",
            workbench=wb["id"],
            user=env["viewer"],
            project=env["project"],
            settings={},
            revision=viewer_ctx["revision"],
        )
    with pytest.raises(RuntimeError, match="workspace_connection_denied"):
        pg.wb_rpc(
            "connection_acquire",
            id=str(uuid4()),
            workbench=wb["id"],
            generation=wb["generation"],
            user=env["viewer"],
            project=env["project"],
            revision=viewer_ctx["revision"],
        )

    # Stranger fails closed on both read and write operations.
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        pg.wb_rpc(
            "activity",
            workbench=wb["id"],
            generation=wb["generation"],
            user=stranger,
            project=env["project"],
            revision=ctx["revision"],
            write=False,
        )
    with pytest.raises(RuntimeError, match="workspace_connection_denied"):
        pg.wb_rpc(
            "connection_acquire",
            id=str(uuid4()),
            workbench=wb["id"],
            generation=wb["generation"],
            user=stranger,
            project=env["project"],
            revision=ctx["revision"],
        )

    # Non-default automation workbench stays strictly owner-scoped.
    automation = pg.wb_rpc(
        "create",
        user=env["owner"],
        project=env["project"],
        name="Personal automation",
        target_ref="refs/heads/workbench/personal",
        revision=ctx["revision"],
    )
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        pg.wb_rpc(
            "acquire",
            workbench=automation["id"],
            user=env["second_admin"],
            project=env["project"],
            binding={"provider": "docker", "artifact": "pinned-artifact"},
        )


def test_agent_invitation_requires_admin_and_allows_second_project_admin(pg):
    env = seed_project(
        pg,
        extra_members=[("second_admin", "admin"), ("editor", "editor"), ("viewer", "viewer")],
    )
    stranger = str(uuid4())
    pg.sql(f"INSERT INTO auth.users(id) VALUES ('{stranger}');")

    wb = make_running_workbench(pg, env["owner"], env["project"])

    # Non-admins and strangers cannot pass access_assert_owner or create an agent link.
    for denied_user in (env["editor"], env["viewer"], stranger):
        with pytest.raises(RuntimeError, match="access_denied"):
            pg.call_rpc(
                "access_assert_owner",
                user=denied_user,
                project=env["project"],
                workbench=wb["id"],
            )
        with pytest.raises(RuntimeError, match="access_denied"):
            pg.call_rpc(
                "access_agent_link_command",
                action="create",
                user=denied_user,
                payload={
                    "id": str(uuid4()),
                    "project_id": env["project"],
                    "workbench_id": wb["id"],
                    "admission": "sign_in",
                    "key_hash": uuid4().hex * 2,
                },
            )

    # A second project admin (not the workbench's original user_id) CAN delegate the shared workspace.
    org = pg.call_rpc(
        "access_assert_owner",
        user=env["second_admin"],
        project=env["project"],
        workbench=wb["id"],
    )
    assert org == env["org"]

    link_id = str(uuid4())
    created_link = pg.call_rpc(
        "access_agent_link_command",
        action="create",
        user=env["second_admin"],
        payload={
            "id": link_id,
            "project_id": env["project"],
            "workbench_id": wb["id"],
            "admission": "sign_in",
            "key_hash": uuid4().hex * 2,
        },
    )
    assert created_link["id"] == link_id
    assert created_link["workbench_id"] == wb["id"]

    # External agent workbenches remain strictly scoped to the admin who owns them.
    ext_reg = str(uuid4())
    pg.sql(
        "INSERT INTO public.cloud_workbenches(user_id,project_id,name,target_ref,agent_registration_id) "
        f"VALUES ('{env['owner']}','{env['project']}','External Agent',"
        f"'refs/heads/agents/{ext_reg}','{ext_reg}');"
    )
    ext_wb = pg.row(
        f"SELECT * FROM public.cloud_workbenches WHERE agent_registration_id='{ext_reg}'"
    )
    assert (
        pg.call_rpc(
            "access_assert_owner",
            user=env["owner"],
            project=env["project"],
            workbench=ext_wb["id"],
        )
        == env["org"]
    )
    with pytest.raises(RuntimeError, match="access_denied"):
        pg.call_rpc(
            "access_assert_owner",
            user=env["second_admin"],
            project=env["project"],
            workbench=ext_wb["id"],
        )


def test_deleting_creator_preserves_shared_workspace_and_next_open_transfers_actor(pg):
    env = seed_project(pg, extra_members=[("first_editor", "editor"), ("second_editor", "editor")])
    ctx = pg.wb_rpc("context", user=env["first_editor"], project=env["project"])
    shared = pg.wb_rpc(
        "default", user=env["first_editor"], project=env["project"], revision=ctx["revision"]
    )
    personal = pg.wb_rpc(
        "create",
        user=env["first_editor"],
        project=env["project"],
        name="Personal",
        target_ref="refs/heads/workbench/personal",
        revision=ctx["revision"],
    )

    # Remove the member who first created the shared workspace and delete their auth.users row.
    pg.sql(
        f"DELETE FROM public.project_members WHERE user_id='{env['first_editor']}'; "
        f"DELETE FROM public.org_members WHERE user_id='{env['first_editor']}'; "
        f"DELETE FROM auth.users WHERE id='{env['first_editor']}';"
    )

    shared_after = pg.row(f"SELECT * FROM cloud_workbenches WHERE id='{shared['id']}'")
    personal_after = pg.row(f"SELECT * FROM cloud_workbenches WHERE id='{personal['id']}'")
    assert shared_after["owner_deleted_at"] is None
    assert personal_after["owner_deleted_at"] is not None

    # Another editor can open the shared workspace and becomes its active actor user_id.
    editor_ctx = pg.wb_rpc("context", user=env["second_editor"], project=env["project"])
    opened = pg.wb_rpc(
        "request_open_in_pool",
        workbench=shared["id"],
        user=env["second_editor"],
        project=env["project"],
        binding={"provider": "docker", "artifact": "pinned-artifact"},
        revision=editor_ctx["revision"],
        pool="default",
    )
    assert opened["open_requested"] is True
    assert opened["user_id"] == env["second_editor"]

    # Project deletion still marks the shared default workspace as owner_deleted_at.
    pg.sql(f"UPDATE public.projects SET lifecycle_status='deleting' WHERE id='{env['project']}';")
    deleted_after = pg.row(f"SELECT * FROM cloud_workbenches WHERE id='{shared['id']}'")
    assert deleted_after["owner_deleted_at"] is not None


def test_populated_upgrade_demotes_duplicate_defaults_keeping_most_recently_active(pg):
    spec = importlib.util.spec_from_file_location(
        "shared_workspace_upgrade_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    database = "shared_ws_upgrade_" + uuid4().hex
    pg.sql(f"CREATE DATABASE {database}")
    scratch = NativeDatabase(pg.url.rsplit("/", 1)[0] + "/" + database)
    try:
        scratch.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text(encoding="utf-8"))
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            if path.name >= MIGRATION.name:
                break
            scratch.sql(module.product_migration_sql(path))

        env = seed_project(
            scratch, extra_members=[("member_b", "editor"), ("member_c", "admin")]
        )
        # Under pre-migration schema, each user gets their own default workbench with internet_access=true.
        wbs = {}
        for key in ("owner", "member_b", "member_c"):
            ctx = scratch.wb_rpc("context", user=env[key], project=env["project"])
            wbs[key] = scratch.wb_rpc(
                "default", user=env[key], project=env["project"], revision=ctx["revision"]
            )
        assert len({wb["id"] for wb in wbs.values()}) == 3

        # Make member_b the most recently active default workbench.
        scratch.sql(
            f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '2 hours' WHERE id='{wbs['owner']['id']}';"
            f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '5 minutes' WHERE id='{wbs['member_b']['id']}';"
            f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '1 day' WHERE id='{wbs['member_c']['id']}';"
        )

        # Rollback dry run leaves all three defaults intact.
        scratch.sql(MIGRATION.read_text(encoding="utf-8").replace("COMMIT;", "ROLLBACK;"))
        assert (
            scratch.row(
                f"SELECT count(*) AS n FROM cloud_workbenches WHERE project_id='{env['project']}' AND is_default"
            )["n"]
            == 3
        )

        # Apply the migration for real.
        scratch.sql(MIGRATION.read_text(encoding="utf-8"))

        rows = {
            key: scratch.row(f"SELECT * FROM cloud_workbenches WHERE id='{wb['id']}'")
            for key, wb in wbs.items()
        }
        # All rows preserved (no data deleted).
        assert all(row is not None for row in rows.values())
        # Only most recently active row remains default with internet_access=true.
        assert rows["member_b"]["is_default"] is True
        assert rows["member_b"]["internet_access"] is True
        # Older rows are demoted to non-default with internet_access=false.
        for demoted in ("owner", "member_c"):
            assert rows[demoted]["is_default"] is False
            assert rows[demoted]["internet_access"] is False

        # Every project member now resolves to the single shared default workspace.
        for key in ("owner", "member_b", "member_c"):
            ctx = scratch.wb_rpc("context", user=env[key], project=env["project"])
            assert ctx["workbench"]["id"] == wbs["member_b"]["id"]

        # Complete any later migrations and verify contract checks pass.
        for path in sorted(MIGRATION.parent.glob("*.sql")):
            if path.name > MIGRATION.name:
                scratch.sql(module.product_migration_sql(path))
        scratch.sql(
            (ROOT / "supabase/tests/_support/cloud_agent_contracts.inc").read_text(encoding="utf-8")
        )
    finally:
        pg.sql(f"DROP DATABASE {database} WITH (FORCE)")
