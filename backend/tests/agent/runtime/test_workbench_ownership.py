"""Durable ownership and resource fencing, on an owned real PostgreSQL cluster."""

import concurrent.futures
import json
from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import literal

pytestmark = pytest.mark.integration


def call(database, function, **arguments):
    parameters = ",".join(f"p_{key}=>{literal(value)}" for key, value in arguments.items())
    return json.loads(
        database.sql(f"SELECT to_jsonb(public.cloud_workbench_{function}({parameters}));")
    )


def create(database, args, **changes):
    return call(
        database,
        "create",
        user=args["user"],
        project=args["project"],
        name="Fixture computer",
        target_ref="refs/heads/workbench/fixture",
        revision=args["policy"]["revision"],
        **changes,
    )


def transition(database, row, state, resource=None):
    return call(
        database,
        "transition",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
        state=state,
        resource=resource,
    )


def test_workbench_is_not_unique_per_actor_or_session(postgres, submitted):
    args, legacy = submitted
    first, second = create(postgres, args), create(postgres, args)
    assert first["id"] != second["id"]
    assert first["user_id"] == args["user"] and first["project_id"] == args["project"]
    assert first["policy"] == "agent-sealed" and first["internet_access"] is False
    assert first["state"] == "retired" and first["resource"] is None
    assert (
        postgres.row(f"SELECT * FROM agent_runs WHERE id='{legacy['id']}'")["session_id"]
        == legacy["session_id"]
    )


def test_workbench_allocation_is_single_flight_and_ack_is_fenced(postgres, submitted):
    args, _ = submitted
    first = create(postgres, args)
    binding = {"provider": "docker", "artifact": "fixture"}

    def acquire(_):
        return call(
            postgres,
            "acquire",
            workbench=first["id"],
            user=args["user"],
            project=args["project"],
            binding=binding,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(acquire, range(8)))
    winners = [row for row in rows if row["acquired"]]
    assert len(winners) == 1
    row = winners[0]["workbench"]
    assert all(item["workbench"]["generation"] == row["generation"] for item in rows)
    intent = {
        "provider": "docker",
        "resource_id": "owned-fixture",
        "workbench_id": row["id"],
        "workbench_generation": row["generation"],
        "allocation_pending": True,
    }
    allocating = transition(postgres, row, "allocating", intent)
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        transition(postgres, row, "running", {**intent, "allocation_pending": False})
    running = transition(postgres, allocating, "running", {**intent, "allocation_pending": False})
    assert running["generation"] == row["generation"]
    assert (
        call(
            postgres,
            "acquire",
            workbench=first["id"],
            user=args["user"],
            project=args["project"],
            binding=binding,
        )["acquired"]
        is False
    )


def test_lost_allocation_ack_retains_exact_intent_for_takeover(postgres, submitted):
    args, _ = submitted
    created = create(postgres, args)
    arguments = dict(
        workbench=created["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": "docker", "artifact": "fixture"},
    )
    first = call(postgres, "acquire", **arguments)["workbench"]
    intent = {
        "workbench_id": first["id"],
        "workbench_generation": first["generation"],
        "provider": "docker",
        "resource_id": "same-allocation",
        "allocation_pending": True,
    }
    first = transition(postgres, first, "allocating", intent)
    postgres.sql(
        f"UPDATE cloud_workbenches SET operation_until=clock_timestamp()-interval '1 second' WHERE id='{first['id']}'"
    )
    second = call(postgres, "acquire", **arguments)
    assert second["acquired"] and second["workbench"]["resource"] == intent
    assert second["workbench"]["generation"] == first["generation"]
    assert second["workbench"]["operation_id"] != first["operation_id"]
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        transition(postgres, first, "running", {**intent, "allocation_pending": False})


def test_cross_actor_project_and_revoked_creation_rejected(postgres, submitted):
    args, _ = submitted
    row = create(postgres, args)
    for user, project in [
        (str(uuid4()), args["project"]),
        (args["user"], str(uuid4())),
        (None, args["project"]),
        (args["user"], None),
    ]:
        with pytest.raises(RuntimeError, match="workbench_unavailable"):
            call(
                postgres,
                "acquire",
                workbench=row["id"],
                user=user,
                project=project,
                binding={"provider": "docker", "artifact": "fixture"},
            )
    postgres.sql(
        f"UPDATE projects SET name='permission revision changes' WHERE id='{args['project']}'"
    )
    # A deliberately invalid revision is not allowed to create an owned computer.
    with pytest.raises(RuntimeError):
        call(
            postgres,
            "create",
            user=args["user"],
            project=args["project"],
            name="Rejected",
            target_ref="refs/heads/test",
            revision={"project": "stale"},
        )


def test_resource_cannot_be_deleted_before_retirement(postgres, submitted):
    args, _ = submitted
    row = create(postgres, args)
    acquired = call(
        postgres,
        "acquire",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": "docker", "artifact": "fixture"},
    )["workbench"]
    with pytest.raises(RuntimeError, match="workbench_cleanup_required"):
        postgres.sql(f"DELETE FROM cloud_workbenches WHERE id='{row['id']}'")
    with pytest.raises(RuntimeError, match="workbench_transition_invalid"):
        transition(postgres, acquired, "retired")


def test_legacy_session_deletion_does_not_own_independent_computer(postgres, submitted):
    args, run = submitted
    row = create(postgres, args)
    postgres.sql(
        f"UPDATE agent_runs SET state='failed' WHERE id='{run['id']}'; DELETE FROM chat_sessions WHERE id='{run['session_id']}'"
    )
    assert (
        postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")["id"] == row["id"]
    )


def test_project_deletion_retains_shared_resource_cleanup_identity(postgres, submitted):
    args, run = submitted
    row = create(postgres, args)
    postgres.sql(
        f"UPDATE agent_runs SET state='failed' WHERE id='{run['id']}'; DELETE FROM projects WHERE id='{args['project']}'"
    )
    retained = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")
    assert retained["owner_deleted_at"] and retained["project_id"] == row["project_id"]
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        call(
            postgres,
            "acquire",
            workbench=row["id"],
            user=args["user"],
            project=args["project"],
            binding={"provider": "docker", "artifact": "fixture"},
        )


@pytest.mark.parametrize("missing", ["generation", "version", "operation"])
def test_null_fences_are_rejected(postgres, submitted, missing):
    args, _ = submitted
    created = create(postgres, args)
    row = call(
        postgres,
        "acquire",
        workbench=created["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": "docker", "artifact": "fixture"},
    )["workbench"]
    parameters = dict(
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
        state="retained",
    )
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        call(postgres, "transition", **{**parameters, missing: None})


def test_public_roles_cannot_access_workbench_control_plane(postgres):
    for role in ["anon", "authenticated"]:
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(f"SET ROLE {role}; SELECT * FROM cloud_workbenches")
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(
                f"SET ROLE {role}; SELECT cloud_workbench_create('{uuid4()}', 'project', 'name', 'refs/heads/main', '{{}}'::jsonb)"
            )
