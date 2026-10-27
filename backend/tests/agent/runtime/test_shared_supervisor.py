"""Real SQL/HTTP/supervisor, synthetic provider port. Not provider acceptance."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.platform.access.adapters.agent.runtime.control.admission import Admission
from src.platform.access.adapters.agent.runtime.control.checkpoints import Checkpoints
from src.platform.access.adapters.agent.runtime.control.runner import RunSupervisor
from src.platform.access.adapters.agent.runtime.control.shared_tasks import SharedTasks
from src.platform.access.adapters.agent.runtime.control.workspace import workspace_state
from src.platform.access.adapters.agent.runtime.execution.sandbox import SandboxExecution
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.platform.access.adapters.agent.runtime.loop.pi import PiLoop
from src.platform.access.adapters.agent.runtime.persistence.commands import RunRepository
from src.platform.access.adapters.agent.runtime.persistence.shared_tasks import SharedTaskRepository
from tests.agent.runtime.test_workbench_attachment import shared_submit
from tests.agent.runtime.test_workbench_task_leases import release, running, task
from tests.agent.runtime.conftest import claimant

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def shared_case(postgres, submitted, control_api, request):
    args, _ = submitted
    control_api.table("access_surfaces").update(
        {
            "config": {
                "llm_model": "fixture",
                "bash_view": {"path_prefix": "", "excludes": [], "max_mode": "rw"},
            }
        }
    ).eq("id", args["agent"]).execute()
    repo = RunRepository(control_api)
    admission = Admission(repo)
    context = repo.load_run_context(args["user"], args["project"], args["agent"])
    policy = admission.evaluate(context, args["user"], args["project"]).policy
    args = {**args, "policy": policy}
    wb = running(postgres, args)
    if getattr(request, "param", None):
        # Lifecycle changed state before this Run was submitted, not under an occupant.
        postgres.sql(f"UPDATE cloud_workbenches SET state='{request.param}' WHERE id='{wb['id']}'")
        wb = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{wb['id']}'")
    shared_submit(postgres, args, wb)
    run = repo.claim_workbench_run(worker=claimant(str(uuid4())))
    tasks = SharedTaskRepository(control_api)
    base = {
        "generation": str(uuid4()),
        "object_format": "sha1",
        "target_ref": wb["target_ref"],
        "expected_oid": None,
    }
    files = {"human": "newer unsaved human content"}
    opened, stopped, starts, controls = [], [], [], []
    stop_error, fail_after_tool = [], []
    receiving = asyncio.Event()
    receiving.set()

    class TaskWorker:
        def __init__(self, execution, project, *, workbench_id, generation, task_uid, **kwargs):
            self.execution_id = self.task_execution_id = execution
            self.project_id, self.task_uid = project, task_uid
            self.resource, self.recovery = {}, None

        async def connect(self, resource, **kwargs):
            durable = postgres.row(
                f"SELECT resource FROM agent_run_executions WHERE id='{self.execution_id}'"
            )
            assert durable["resource"] == resource  # Identity precedes provider access.
            self.resource = copy.deepcopy(resource)
            opened.append(self)

        async def prepare(self, config):
            assert self.restore_point is None
            assert not config["resume_workspace"]

        async def start_loop(self, config):
            starts.append(config)
            checkpoint = {"version": 1, "pi_version": "0.85.1", "entries": [], "leaf_id": None}
            call = {"call_id": "fixture", "name": "bash", "input": {"command": "printf saved"}}
            frames = [
                {
                    "type": "ready",
                    "pi_version": "0.85.1",
                    "node_version": "v22.22.3",
                    "workspace_version": 2,
                    "operation_version": 1,
                },
                {"type": "tool_start", "id": "start", **call, "checkpoint": checkpoint},
                {
                    "type": "tool_end",
                    "id": "end",
                    **call,
                    "result": {"content": [{"type": "text", "text": "saved"}]},
                },
                {
                    "type": "checkpoint",
                    "id": "settled",
                    "reason": "settled",
                    "checkpoint": checkpoint,
                },
                {"type": "finished"},
            ]
            if fail_after_tool:
                frames = [*frames[:3], {"type": "failed"}]
            if config["finalize_only"]:
                frames = [frames[0], frames[-1]]
            self.frames = iter(frames)

        async def receive(self):
            await receiving.wait()
            await asyncio.sleep(0)
            return next(self.frames)

        async def control(self, action, value=None):
            controls.append(action)
            if action in {"finalize", "quiesce"}:
                return {"changed": False, "tip": None}
            raise AssertionError("Unexpected shared control: " + action)

        async def send(self, frame):
            if frame["type"] == "start":
                await self.start_loop(frame["config"])

        async def touch(self):
            pass

        async def stop(self):
            if stop_error:
                raise RuntimeError("provider cleanup unconfirmed")
            stopped.append(self.task_execution_id)

        async def snapshot(self):
            raise AssertionError("Shared filesystem must not be snapshotted by a Run")

    owner = SimpleNamespace(
        ensure_running=AsyncMock(return_value=wb),
        connection=AsyncMock(return_value=None),
        check=AsyncMock(),
    )
    publication = SimpleNamespace(
        capture=lambda r, g: workspace_state(r, base),
        publish=AsyncMock(return_value={"status": "no_changes"}),
    )
    forbidden = AsyncMock(side_effect=AssertionError("Run touched whole-computer ownership"))
    billing = SimpleNamespace(
        start_session=forbidden,
        resume_session=forbidden,
        finish_session=forbidden,
        detach_session=forbidden,
    )
    lifecycle = SimpleNamespace(cleanup=forbidden, attach=forbidden)

    def supervisor():
        return RunSupervisor(
            repo,
            admission,
            publication,
            None,
            billing,
            loop=PiLoop(),
            execution=SandboxExecution(),
            filesystem=SandboxFilesystem(),
            worker_factory=None,
            worker_lifecycle=lifecycle,
            shared_tasks=SharedTasks(tasks, owner, worker_factory=TaskWorker),
        )

    return SimpleNamespace(**locals())


async def test_existing_supervisor_executes_shared_run_without_vm_side_effects(shared_case):
    case = shared_case
    other = shared_submit(case.postgres, case.args, case.wb)
    other = case.repo.claim_workbench_run(worker=claimant("other"))
    untouched = task(case.postgres, other, case.wb)
    await case.supervisor().run_claim(case.run)
    final = case.repo.get(case.run["id"])
    assert final["state"] == "succeeded", final
    assert final["billing_run_id"] is None
    assert case.stopped == [case.run["execution_id"]]
    assert case.tasks.active(case.run) == []
    assert case.tasks.active(other) == [untouched]
    assert (
        case.postgres.row(f"SELECT state FROM cloud_workbenches WHERE id='{case.wb['id']}'")[
            "state"
        ]
        == "running"
    )
    assert case.files == {"human": "newer unsaved human content"}
    assert case.repo.tools(final["id"])[0]["result"]["checkpoint"]["state"]["recovery"] is None
    case.forbidden.assert_not_awaited()
    case.owner.check.assert_awaited()
    case.owner.ensure_running.assert_not_awaited()  # Already-running workspaces need no lifecycle wake.


@pytest.mark.parametrize(
    "shared_case",
    ["retired", "allocating", "paused", "resuming", "retained", "cleanup_pending"],
    indirect=True,
)
async def test_lifecycle_failure_prevents_task_attachment(shared_case):
    case = shared_case
    with pytest.raises(RuntimeError, match="already be running"):
        await SharedTasks(case.tasks, case.owner, worker_factory=case.TaskWorker).acquire(
            case.run, workspace_state(case.run, case.base)
        )
    case.owner.ensure_running.assert_awaited_once()
    case.owner.connection.assert_not_awaited()
    case.owner.check.assert_not_awaited()
    assert case.tasks.active(case.run) == []


async def test_workspace_is_private_even_when_another_user_has_the_same_project(shared_case):
    case = shared_case
    other_user_run = {**case.run, "user_id": str(uuid4())}
    with pytest.raises(PermissionError, match="ownership mismatch"):
        await SharedTasks(case.tasks, case.owner, worker_factory=case.TaskWorker).acquire(
            other_user_run, workspace_state(case.run, case.base)
        )
    case.owner.ensure_running.assert_not_awaited()
    case.owner.connection.assert_not_awaited()
    case.owner.check.assert_not_awaited()
    assert case.tasks.active(case.run) == []


async def test_takeover_quiesces_predecessor_before_allocating_new_uid(shared_case):
    case = shared_case
    old = task(case.postgres, case.run, case.wb)
    case.postgres.sql(
        f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{case.run['id']}'"
    )
    current = case.repo.claim_workbench_run(worker=claimant("takeover"))
    await case.supervisor().run_claim(current)
    assert case.repo.get(current["id"])["state"] == "succeeded"
    assert case.stopped == [old["execution_id"], current["execution_id"]]
    assert case.opened[-1].task_uid > old["task_uid"]
    case.forbidden.assert_not_awaited()


async def test_cleanup_failure_is_durable_and_terminal_retry_only_cleans_own_task(shared_case):
    case = shared_case
    case.stop_error.append(True)
    await case.supervisor().run_claim(case.run)
    final = case.repo.get(case.run["id"])
    assert final["state"] == "succeeded"  # Confirmed publication is not undone by cleanup.
    assert final["snapshot"]["resource_retained"] is True
    assert len(case.tasks.active(case.run)) == 1
    # Retained cleanup is retried once its backoff (dispatch_after) elapses.
    case.postgres.sql(
        f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second',dispatch_after=clock_timestamp()-interval '1 second' WHERE id='{final['id']}'"
    )
    current = case.repo.claim_workbench_run(worker=claimant("cleanup"))
    case.stop_error.clear()
    await case.supervisor().run_claim(current)
    assert len(case.starts) == 1
    assert case.tasks.active(current) == []
    assert case.repo.get(current["id"])["snapshot"]["resource_retained"] is False
    case.forbidden.assert_not_awaited()


async def test_unknown_tool_does_not_replay_or_restore_shared_files(shared_case):
    case = shared_case
    task(case.postgres, case.run, case.wb)
    value = {**PiLoop().initial_state(), **workspace_state(case.run, case.base)}
    value["workbench"] = {"id": case.wb["id"], "generation": case.wb["generation"]}
    manifest = await Checkpoints().save(case.run, value)
    case.repo.begin_tool(
        case.run,
        {"call_id": "ambiguous", "name": "bash", "input": {"command": "printf maybe"}},
        checkpoint=manifest,
        approval_required=False,
    )
    case.postgres.sql(
        f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{case.run['id']}'"
    )
    current = case.repo.claim_workbench_run(worker=claimant("unknown"))
    await case.supervisor().run_claim(current)
    assert case.repo.get(current["id"])["state"] == "outcome_unknown"
    assert case.starts == []
    assert case.files["human"] == "newer unsaved human content"
    assert case.tasks.active(current) == []
    case.publication.publish.assert_not_awaited()
    case.forbidden.assert_not_awaited()


async def test_supervisor_cancellation_only_stops_its_captured_task(shared_case):
    case = shared_case
    case.receiving.clear()
    current = asyncio.create_task(case.supervisor().run_claim(case.run))
    async with asyncio.timeout(5):
        while not case.starts:
            await asyncio.sleep(0.01)
    current.cancel()
    with pytest.raises(asyncio.CancelledError):
        await current
    assert case.stopped == [case.run["execution_id"]]
    # A successor rechecks/ACKs this identity; cancellation never fabricates a DB ACK.
    assert len(case.tasks.active(case.run)) == 1
    case.forbidden.assert_not_awaited()


async def test_known_failed_run_continues_conversation_without_a_filesystem_copy(shared_case):
    case = shared_case
    case.fail_after_tool.append(True)
    await case.supervisor().run_claim(case.run)
    first = case.repo.get(case.run["id"])
    assert first["state"] == "failed"
    assert first["publication"] is None
    case.fail_after_tool.clear()
    shared_submit(case.postgres, case.args, case.wb, session=first["session_id"])
    following = case.repo.claim_workbench_run(worker=claimant("next-message"))
    await case.supervisor().run_claim(following)
    assert case.repo.get(following["id"])["state"] == "succeeded"
    assert case.starts[-1]["entries"] == first["checkpoint"]["state"]["entries"]
    assert case.starts[-1]["resume_workspace"] is False
    assert case.files["human"] == "newer unsaved human content"
    case.forbidden.assert_not_awaited()


async def test_revocation_rejects_admission_but_preserves_task_cleanup_authority(shared_case):
    case = shared_case
    task(case.postgres, case.run, case.wb)
    case.postgres.sql(f"UPDATE access_surfaces SET status='paused' WHERE id='{case.args['agent']}'")
    await case.supervisor().run_claim(case.run)
    assert case.repo.get(case.run["id"])["state"] == "failed"
    case.owner.ensure_running.assert_not_awaited()
    assert case.stopped == [case.run["execution_id"]]
    assert case.tasks.active(case.run) == []
    assert case.starts == []


async def test_uncertain_provider_connection_keeps_reserved_identity_for_cleanup(shared_case):
    case = shared_case
    case.owner.connection.side_effect = RuntimeError("connection unavailable; must not wake it")
    await case.supervisor().run_claim(case.run)
    final = case.repo.get(case.run["id"])
    assert final["snapshot"]["resource_retained"] is True
    assert len(case.tasks.active(final)) == 1
    assert case.starts == [] and case.stopped == []
    case.forbidden.assert_not_awaited()


async def test_publication_retry_keeps_original_candidate_and_skips_model_and_git_finalize(
    shared_case,
):
    case = shared_case
    task(case.postgres, case.run, case.wb)
    value = {**PiLoop().initial_state(), **workspace_state(case.run, case.base)}
    value.update(
        workbench={"id": case.wb["id"], "generation": case.wb["generation"]},
        reason="settled",
        publication_candidate=case.run["id"],
    )
    value["workspace"].update(changed=True, tip="a" * 40)
    manifest = await Checkpoints().save(case.run, value)
    case.repo.write(case.run, "checkpoint", checkpoint=manifest, state="publishing")
    case.postgres.sql(
        f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{case.run['id']}'"
    )
    current = case.repo.claim_workbench_run(worker=claimant("publish-retry"))
    await case.supervisor().run_claim(current)
    assert case.repo.get(current["id"])["state"] == "succeeded"
    assert case.starts[0]["finalize_only"] is True
    assert case.repo.tools(current["id"]) == []
    assert case.controls == ["quiesce"]
    candidate = case.publication.publish.call_args.args[1]
    assert candidate["workspace"]["tip"] == "a" * 40
    assert candidate["publication_candidate"] == case.run["id"]
    assert case.files["human"] == "newer unsaved human content"


async def test_fenced_coordinator_cannot_discover_or_stop_successor(shared_case):
    case = shared_case
    old = task(case.postgres, case.run, case.wb)
    case.postgres.sql(
        f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{case.run['id']}'"
    )
    current = case.repo.claim_workbench_run(worker=claimant("new-owner"))
    release(case.postgres, current, old)
    successor = task(case.postgres, current, case.wb)
    await SharedTasks(case.tasks, case.owner, worker_factory=case.TaskWorker).close(case.run)
    assert case.opened == [] and case.stopped == []
    assert case.tasks.active(current) == [successor]


@pytest.mark.parametrize("damage", ["snapshot", "generation", "target"])
async def test_incompatible_checkpoint_rejected_before_lifecycle_access(shared_case, damage):
    case = shared_case
    value = {**PiLoop().initial_state(), **workspace_state(case.run, case.base)}
    if damage == "snapshot":
        value["recovery"] = {"id": "legacy-snapshot"}
    elif damage == "generation":
        value["workbench"] = {"id": case.wb["id"], "generation": str(uuid4())}
    else:
        value["base"] = {**value["base"], "target_ref": "refs/heads/another"}
    with pytest.raises(PermissionError):
        await SharedTasks(case.tasks, case.owner, worker_factory=case.TaskWorker).acquire(
            case.run, value
        )
    case.owner.ensure_running.assert_not_awaited()
    assert case.tasks.active(case.run) == []


async def test_unconfigured_supervisor_rejects_shared_claim_before_any_cleanup(shared_case):
    case = shared_case
    value = RunSupervisor(
        case.repo,
        case.admission,
        case.publication,
        None,
        case.billing,
        loop=PiLoop(),
        execution=SandboxExecution(),
        filesystem=SandboxFilesystem(),
        worker_factory=None,
        worker_lifecycle=case.lifecycle,
    )
    with pytest.raises(PermissionError, match="shared"):
        await value.run_claim(case.run)
    case.forbidden.assert_not_awaited()
