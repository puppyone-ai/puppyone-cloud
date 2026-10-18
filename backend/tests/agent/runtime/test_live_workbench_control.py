"""Real populated PostgreSQL ownership/lifecycle fencing, independent of providers."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import literal

pytestmark = pytest.mark.integration


def rpc(db, function, **args):
    parameters = ",".join("p_" + key + "=>" + literal(value) for key, value in args.items())
    return db.row(f"SELECT public.cloud_workbench_{function}({parameters}) AS result")["result"]


def default(db, args):
    revision = rpc(db, "context", user=args["user"], project=args["project"])["revision"]
    return rpc(db, "default", user=args["user"], project=args["project"], revision=revision)


def claim(db, args, row, provider="docker"):
    return rpc(
        db,
        "acquire",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": provider, "artifact": "pinned-artifact"},
    )


def transition(db, row, state, resource=None):
    return rpc(
        db,
        "transition",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
        state=state,
        resource=resource,
    )


def running(db, args):
    row = claim(db, args, default(db, args))["workbench"]
    return transition(
        db,
        row,
        "running",
        {
            "provider": "docker",
            "resource_id": "exact-resource",
            "workbench_id": row["id"],
            "workbench_generation": row["generation"],
            "allocation_pending": False,
            "project_id": args["project"],
        },
    )


def test_concurrent_chats_resolve_one_default_but_automations_do_not(postgres, submitted):
    args, _ = submitted
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: default(postgres, args), range(4)))
    assert len({row["id"] for row in rows}) == 1
    assert rows[0]["target_ref"] == "refs/heads/workspaces/" + args["user"]
    context = rpc(postgres, "context", user=args["user"], project=args["project"])
    request = str(uuid4())
    auto = rpc(
        postgres,
        "automation",
        user=args["user"],
        project=args["project"],
        request=request,
        revision=context["revision"],
    )
    again = rpc(
        postgres,
        "automation",
        user=args["user"],
        project=args["project"],
        request=request,
        revision=context["revision"],
    )
    assert auto["id"] == again["id"] != rows[0]["id"]
    assert not auto["is_default"]
    assert (
        rpc(postgres, "context", user=args["user"], project=args["project"])["workbench"]["id"]
        == rows[0]["id"]
    )


def test_default_is_private_per_user_even_with_same_project(postgres, submitted):
    args, _ = submitted
    first = default(postgres, args)
    other = str(uuid4())
    postgres.sql(f"INSERT INTO auth.users(id) VALUES ('{other}')")
    second = default(postgres, {**args, "user": other})
    assert first["id"] != second["id"]
    assert first["target_ref"] != second["target_ref"]


def test_provider_binding_cannot_be_changed_by_new_default(postgres, submitted):
    args, _ = submitted
    row = claim(postgres, args, default(postgres, args), provider="fly")["workbench"]
    assert row["binding"]["provider"] == "fly"
    with pytest.raises(RuntimeError, match="workbench_binding_mismatch"):
        claim(postgres, args, row, provider="e2b")
    postgres.sql(
        f"UPDATE cloud_workbenches SET operation_until=now()-interval '1 second' WHERE id='{row['id']}'"
    )
    retry = claim(postgres, args, row, provider="fly")["workbench"]
    assert retry["generation"] == row["generation"]
    assert retry["compute_epoch"] == row["compute_epoch"]


def test_activity_prevents_pause_without_changing_generation(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    postgres.sql(
        f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '1 hour' WHERE id='{row['id']}'"
    )
    context = rpc(postgres, "context", user=args["user"], project=args["project"])
    active = rpc(
        postgres,
        "activity",
        user=args["user"],
        project=args["project"],
        workbench=row["id"],
        generation=row["generation"],
        revision=context["revision"],
        write=True,
    )
    assert active["unpublished_changes"]
    assert active["generation"] == row["generation"]
    idle = rpc(
        postgres,
        "idle_claim",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        action="pause",
    )
    assert not idle["acquired"]
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        rpc(
            postgres,
            "activity",
            user=args["user"],
            project=args["project"],
            workbench=row["id"],
            generation=str(uuid4()),
            revision=context["revision"],
        )


def test_expired_pause_takeover_preserves_files_and_fences_old_ack(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    pause = rpc(
        postgres,
        "suspend",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
    )["workbench"]
    first = rpc(
        postgres,
        "reconcile_claim",
        workbench=row["id"],
        generation=row["generation"],
        version=pause["version"],
    )
    assert not first["acquired"]
    postgres.sql(
        f"UPDATE cloud_workbenches SET operation_until=now()-interval '1 second' WHERE id='{row['id']}'"
    )
    taken = rpc(
        postgres,
        "reconcile_claim",
        workbench=row["id"],
        generation=row["generation"],
        version=pause["version"],
    )
    assert taken["acquired"]
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        transition(postgres, pause, "paused")
    paused = transition(postgres, taken["workbench"], "paused")
    resumed = claim(postgres, args, paused)["workbench"]
    assert resumed["generation"] == row["generation"]
    assert resumed["resource"] == row["resource"]
    assert resumed["compute_epoch"] == row["compute_epoch"] + 1


@pytest.mark.asyncio
async def test_product_owner_single_flight_native_files_and_revocation(
    postgres, submitted, control_api, monkeypatch
):
    import asyncio
    import base64
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from src.exceptions import NotFoundException
    from src.platform.scope_sandbox.workbench_repository import WorkbenchRepository
    from src.platform.scope_sandbox.workbench_service import WorkbenchService
    from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem

    args, _ = submitted
    selected, allocations, initializations, channels = ["docker"], [], [], []
    revision = "c" * 64

    class Provider:
        name = "docker"
        capabilities = SimpleNamespace(shared_workspace=True)

        def artifact(self, image):
            return "pinned-image"

        def validate(self, artifact, resource=None):
            assert artifact == "pinned-image"

        async def running_connection(self, host):
            return None

    provider = Provider()

    class Channel:
        def __init__(self, row, *, purpose="lifecycle"):
            assert row["binding"]["provider"] == "docker"
            self.row, self.purpose, self.runtime = row, purpose, provider
            self.resource = row["resource"] or dict(
                provider="docker",
                resource_id="same-computer",
                workbench_id=row["id"],
                workbench_generation=row["generation"],
                project_id=row["project_id"],
                allocation_pending=True,
            )
            channels.append(purpose)

        async def create(self):
            allocations.append(self.row["id"])
            await asyncio.sleep(0.05)
            self.resource["allocation_pending"] = False

        async def _open_control(self):
            pass

        async def connect(self, connection):
            pass

        async def close(self):
            pass

        async def control(self, action, value=None):
            if action == "capabilities":
                return {"workbench_lifecycle": 1}
            if action == "initialize":
                initializations.append(value)
                return {}
            if action == "files":
                if value["action"] == "write":
                    assert (
                        postgres.sql(
                            "SELECT status FROM audit_logs WHERE source_channel='workbench' ORDER BY id DESC LIMIT 1"
                        )
                        == "intent"
                    )
                    return {"result": {"revision": revision}}
                assert value == {"action": "read", "path": "live.md"}
                return {
                    "result": {
                        "content_base64": base64.b64encode(b"uncommitted agent edit").decode(),
                        "revision": revision,
                    }
                }
            raise AssertionError(action)

    def providers(name):
        assert name in (None, "docker")
        # Changing the deployment default later must not affect a bound workspace.
        if name is None:
            assert selected[0] == "docker"
        return provider

    class Transport:
        def describe(self, project, grant, target_ref=None):
            return dict(
                project_id=project,
                generation=str(uuid4()),
                object_format="sha1",
                target_ref=target_ref or "refs/heads/main",
                expected_oid=None,
            )

    billing = SimpleNamespace(
        ensure_session=AsyncMock(), check_session=AsyncMock(), detach_session=AsyncMock()
    )
    service = WorkbenchService(
        Transport(),
        SandboxFilesystem(),
        billing,
        repository=WorkbenchRepository(control_api),
        channel_factory=Channel,
        providers=providers,
    )
    assert await service.current(args["user"], args["project"]) is None
    assert allocations == []
    first, second = await asyncio.gather(
        service.open(args["user"], args["project"]), service.open(args["user"], args["project"])
    )
    assert first["id"] == second["id"] and len(allocations) == 1
    assert len(initializations) == 1 and billing.ensure_session.await_count == 1
    selected[0] = "fly"
    reused = await service.open(args["user"], args["project"])
    assert reused["resource"] == first["resource"] and len(allocations) == 1
    data = await service.file_operation(
        args["user"], args["project"], first["generation"], {"action": "read", "path": "live.md"}
    )
    assert base64.b64decode(data["content_base64"]) == b"uncommitted agent edit"
    assert "files" in channels
    await service.file_operation(
        args["user"],
        args["project"],
        first["generation"],
        {
            "action": "write",
            "path": "live.md",
            "expected_revision": revision,
            "content_base64": base64.b64encode(b"human draft").decode(),
        },
    )
    audits = postgres.row(
        f"SELECT jsonb_agg(a ORDER BY id) AS rows FROM audit_logs a WHERE project_id='{args['project']}'"
    )["rows"]
    assert [entry["status"] for entry in audits] == ["intent", "completed"]
    assert audits[0]["metadata"]["operation_id"] == audits[1]["metadata"]["operation_id"]
    assert "content_base64" not in str(audits)
    with pytest.raises(HTTPException) as stale:
        await service.file_operation(
            args["user"], args["project"], str(uuid4()), {"action": "read", "path": "live.md"}
        )
    assert stale.value.status_code == 409
    before = len(channels)
    postgres.sql(f"UPDATE projects SET lifecycle_status='deleting' WHERE id='{args['project']}';")
    with pytest.raises(NotFoundException):
        await service.file_operation(
            args["user"],
            args["project"],
            first["generation"],
            {"action": "read", "path": "live.md"},
        )
    assert len(channels) == before
