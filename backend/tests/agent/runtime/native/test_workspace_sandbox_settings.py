"""Workspace owners store their Cloud Computer choices through one fenced command.

``cloud_workbench_set_sandbox_settings`` checks the authorization revision and
ownership like opening does; the table constraint accepts only the known
boolean keys, so no caller can widen what a workspace asks its machine for.
"""

import json

import pytest

from tests.agent.runtime.conftest import literal


def call(db, name, *values):
    output = db.sql(f"SELECT to_jsonb(public.{name}({','.join(map(literal, values))}));")
    return json.loads(output) if output else None


def owned_workspace(db, args):
    context = call(db, "cloud_workbench_context", args["user"], args["project"])
    row = call(db, "cloud_workbench_default", args["user"], args["project"], context["revision"])
    return row, context["revision"]


def test_owner_settings_are_stored_and_returned(postgres, submitted):
    args, _ = submitted
    row, revision = owned_workspace(postgres, args)
    assert row["sandbox_settings"] == {}
    stored = call(
        postgres,
        "cloud_workbench_set_sandbox_settings",
        row["id"],
        args["user"],
        args["project"],
        {"desktop": True, "screen_stream": False},
        revision,
    )
    assert stored["sandbox_settings"] == {"desktop": True, "screen_stream": False}
    assert stored["binding"] == row["binding"] and stored["generation"] == row["generation"]


@pytest.mark.parametrize(
    "value", [{"browser": True}, {"desktop": "yes"}, {"screen_stream": None}, []]
)
def test_the_table_accepts_only_known_boolean_choices(postgres, submitted, value):
    args, _ = submitted
    row, revision = owned_workspace(postgres, args)
    with pytest.raises(RuntimeError, match="sandbox_settings_shape|violates check"):
        call(
            postgres,
            "cloud_workbench_set_sandbox_settings",
            row["id"],
            args["user"],
            args["project"],
            value,
            revision,
        )


def test_another_user_or_a_stale_revision_is_refused(postgres, submitted):
    args, _ = submitted
    row, revision = owned_workspace(postgres, args)
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        call(
            postgres,
            "cloud_workbench_set_sandbox_settings",
            row["id"],
            "00000000-0000-0000-0000-000000000001",
            args["project"],
            {"desktop": True},
            revision,
        )
    stale = {**revision, "token": "stale"} if isinstance(revision, dict) else "stale"
    with pytest.raises(RuntimeError):
        call(
            postgres,
            "cloud_workbench_set_sandbox_settings",
            row["id"],
            args["user"],
            args["project"],
            {"desktop": True},
            stale,
        )
    unchanged = postgres.row(f"SELECT sandbox_settings FROM cloud_workbenches WHERE id='{row['id']}'")
    assert unchanged["sandbox_settings"] == {}


def test_clients_cannot_execute_the_command(postgres):
    signature = "public.cloud_workbench_set_sandbox_settings(uuid,uuid,text,jsonb,jsonb)"
    for role, allowed in (("anon", False), ("authenticated", False), ("service_role", True)):
        granted = postgres.row(
            f"SELECT has_function_privilege('{role}','{signature}','EXECUTE') AS ok"
        )["ok"]
        assert granted is allowed, role
