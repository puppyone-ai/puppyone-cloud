"""Worker identity, capability hand-back and graceful shutdown, without I/O."""

import asyncio
import os
import signal
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import settings
from src.platform.access.adapters.agent.runtime import dispatch, worker
from src.platform.access.adapters.agent.runtime.control.runner import RunSupervisor
from src.platform.sandbox import WorkspaceUnavailable, bindings


def test_each_process_is_a_new_identity_and_users_never_see_its_host(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_WORKER_POOL", "dev-alice")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "replica-7")
    first, second = dispatch.worker_identity("fly"), dispatch.worker_identity("fly")
    assert first.id != second.id
    claim = first.claim()
    assert claim["pool"] == "dev-alice" and claim["contracts"] == [1, 1]
    assert claim["build"] == "abc123" and claim["host"] == "replica-7"
    assert claim["provider"] == "fly"
    assert first.public() == {"id": first.id, "pool": "dev-alice", "build": "abc123"}


def test_api_admits_into_its_own_pool_with_the_current_contract(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_WORKER_POOL", "dev-alice")
    assert dispatch.run_dispatch() == {"pool": "dev-alice", "contract": dispatch.RUN_CONTRACT}
    low, high = dispatch.SUPPORTED_RUN_CONTRACTS
    assert low <= dispatch.RUN_CONTRACT <= high


def test_pool_names_are_validated_at_startup():
    from typing import Annotated

    from pydantic import TypeAdapter, ValidationError

    field = type(settings).model_fields["AGENT_WORKER_POOL"]
    pool = TypeAdapter(Annotated[field.annotation, *field.metadata])
    assert field.default == "default"
    assert pool.validate_python("dev-alice") == "dev-alice"
    for invalid in ["Alice's laptop", "-leading", "", "x" * 64]:
        with pytest.raises(ValidationError):
            pool.validate_python(invalid)


class FakeProvider:
    def __init__(self, error=None):
        self.error, self.aclose = error, AsyncMock()

    def validate(self, artifact, resource):
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_binding_capability_is_a_local_configuration_check(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(bindings, "runtime_provider", lambda name: provider)
    monkeypatch.setattr(bindings, "workspace_providers", lambda: ("fly",))
    await bindings.require_binding({"provider": "fly", "artifact": "pinned"})
    provider.aclose.assert_awaited_once()

    for binding, options in [
        ({"provider": "unknown", "artifact": "pinned"}, {}),
        ({"provider": "docker", "artifact": "pinned"}, {"workspace_only": True}),
    ]:
        with pytest.raises(WorkspaceUnavailable) as caught:
            await bindings.require_binding(binding, **options)
        assert caught.value.code == bindings.NOT_CONFIGURED

    provider = FakeProvider(ValueError("credentials missing"))
    monkeypatch.setattr(bindings, "runtime_provider", lambda name: provider)
    with pytest.raises(WorkspaceUnavailable) as caught:
        await bindings.require_binding({"provider": "fly", "artifact": "pinned"})
    assert caught.value.code == bindings.NOT_CONFIGURED
    provider.aclose.assert_awaited_once()


def supervisor_for(current, *, executions=()):
    supervisor = RunSupervisor.__new__(RunSupervisor)
    supervisor.shared_tasks = supervisor.worker = None
    supervisor.repo = SimpleNamespace(
        get=Mock(return_value=current),
        release_execution=Mock(),
        tools=Mock(return_value=[]),
        executions=Mock(return_value=list(executions)),
    )
    return supervisor


RUN = {"id": "run", "execution_id": "execution", "fence": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "attempts", "outcome"),
    [
        ("running", 0, None),
        ("running", dispatch.MAX_DISPATCH_ATTEMPTS - 2, None),
        ("running", dispatch.MAX_DISPATCH_ATTEMPTS - 1, ("failed", "no_capable_worker")),
        # Terminal cleanup is never abandoned: it waits for a capable worker.
        ("failed", dispatch.MAX_DISPATCH_ATTEMPTS + 3, None),
    ],
)
async def test_incapable_worker_hands_back_until_no_worker_can_serve(state, attempts, outcome):
    current = {**RUN, "state": state, "dispatch_attempts": attempts}
    supervisor = supervisor_for(current)
    assert await supervisor.hand_back(RUN, bindings.NOT_CONFIGURED) == outcome
    if outcome is None:
        supervisor.repo.release_execution.assert_called_once_with(
            current, reason="worker_incapable"
        )
    else:
        supervisor.repo.release_execution.assert_not_called()


@pytest.mark.asyncio
async def test_superseded_worker_does_not_release_its_successor():
    supervisor = supervisor_for({**RUN, "fence": 4, "state": "running", "dispatch_attempts": 0})
    assert await supervisor.hand_back(RUN, bindings.NOT_CONFIGURED) is None
    supervisor.repo.release_execution.assert_not_called()


def claimed_supervisor(current, *, executions=(), code=bindings.NOT_CONFIGURED):
    supervisor = supervisor_for(current, executions=executions)
    supervisor.models = {}
    idle = lambda: asyncio.sleep(3600)  # noqa: E731
    supervisor.heartbeat = supervisor.text_flush_loop = supervisor.watch_models = idle
    supervisor.execute = AsyncMock(side_effect=WorkspaceUnavailable(code))
    supervisor.preserve_resources = AsyncMock(return_value=True)
    supervisor.finish = AsyncMock()
    return supervisor


@pytest.mark.asyncio
@pytest.mark.parametrize("resources", [[], [{"resource": None}, {"resource": {"id": "vm"}}]])
async def test_no_capable_worker_fails_without_touching_the_provider(resources):
    last = dispatch.MAX_DISPATCH_ATTEMPTS - 1
    current = {**RUN, "state": "running", "dispatch_attempts": last, "publication": None}
    supervisor = claimed_supervisor(current, executions=resources)
    await supervisor._run_claim(RUN)
    supervisor.preserve_resources.assert_not_awaited()
    supervisor.repo.release_execution.assert_not_called()
    # Recorded compute stays retained for a worker that can operate it.
    supervisor.finish.assert_awaited_once_with(
        "failed", "no_capable_worker", retain_resource=bool(resources)
    )


@pytest.mark.asyncio
async def test_released_run_is_not_finished_by_the_incapable_worker():
    current = {**RUN, "state": "running", "dispatch_attempts": 0, "publication": None}
    supervisor = claimed_supervisor(current)
    await supervisor._run_claim(RUN)
    supervisor.repo.release_execution.assert_called_once()
    supervisor.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_failures_are_not_mistaken_for_incapability():
    current = {**RUN, "state": "running", "dispatch_attempts": 0, "publication": None}
    supervisor = claimed_supervisor(current, code="workbench_provider_quota")
    await supervisor._run_claim(RUN)
    supervisor.repo.release_execution.assert_not_called()
    supervisor.finish.assert_awaited_once_with(
        "failed", "workbench_provider_quota", retain_resource=False
    )


def isolate_worker(monkeypatch, repository, supervisor):
    for name, value in {
        "RunRepository": lambda: repository,
        "Admission": lambda _: object(),
        "Publication": lambda transport, filesystem: object(),
        "RunGitTransport": lambda _: object(),
        "build_worker_version_engine_container": lambda **_: SimpleNamespace(repo_manager=None),
        "get_billing_gateway": lambda: None,
        "get_provider_registry": lambda: None,
        "get_inference_service": lambda *_: None,
        "get_runtime_metering_service": lambda: None,
        "RunSupervisor": supervisor,
        "reap_workspaces": AsyncMock(),
        "default_provider": lambda: "docker",
    }.items():
        monkeypatch.setattr(worker, name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("finishes_in_grace", [True, False])
async def test_stop_drains_then_hands_off_unfinished_runs(monkeypatch, finishes_in_grace):
    import threading

    runs = [{"id": name, "execution_id": name, "fence": 1} for name in ("active", "in-flight")]
    stop, started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    claiming, in_flight = threading.Event(), threading.Event()
    claims = []

    def claim(**values):
        claims.append(values["worker"]["pool"])
        if len(claims) == 2:
            # A claim already running when stop arrives still returns a lease.
            claiming.set()
            in_flight.wait(5)
            return runs[1]
        return runs[0] if len(claims) == 1 else None

    repository = SimpleNamespace(
        client=object(), claim_run=claim, claim_workbench_run=claim, release_execution=Mock()
    )

    class Supervisor:
        def __init__(self, *args, **kwargs):
            pass

        async def run_claim(self, run):
            started.set()
            await (release.wait() if finishes_in_grace else asyncio.sleep(3600))

    isolate_worker(monkeypatch, repository, Supervisor)
    monkeypatch.setattr(settings, "AGENT_WORKER_POOL", "dev-alice")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SHUTDOWN_GRACE_SECONDS", 1)
    serving = asyncio.create_task(worker.serve(workbench_owner=None, stop=stop))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert await asyncio.to_thread(claiming.wait, 5)
        stop.set()
        in_flight.set()
        if finishes_in_grace:
            release.set()
        await asyncio.wait_for(serving, 5)
    finally:
        in_flight.set()
        serving.cancel()
        with suppress(asyncio.CancelledError):
            await serving
    assert claims == ["dev-alice", "dev-alice"]  # No claim begins after stop.
    released = [call.args[0]["id"] for call in repository.release_execution.call_args_list]
    if finishes_in_grace:
        assert released == []
    else:
        assert sorted(released) == ["active", "in-flight"]
        assert {call.kwargs["reason"] for call in repository.release_execution.call_args_list} == {
            "worker_shutdown"
        }


@pytest.mark.asyncio
async def test_sigterm_stops_the_worker_and_a_missing_contract_prevents_start(monkeypatch):
    serving, stopped = asyncio.Event(), asyncio.Event()

    async def serve(*, stop):
        serving.set()
        await stop.wait()
        stopped.set()

    monkeypatch.setattr(worker, "serve", serve)
    monkeypatch.setattr(
        worker, "RunRepository", lambda: SimpleNamespace(verify_dispatch_contract=lambda: None)
    )
    main = asyncio.create_task(worker.main())
    await asyncio.wait_for(serving.wait(), 2)  # Signal handlers precede serve().
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(main, 2)
    assert stopped.is_set()

    def outdated():
        raise RuntimeError("agent_run_dispatch is missing")

    serving.clear()
    monkeypatch.setattr(
        worker, "RunRepository", lambda: SimpleNamespace(verify_dispatch_contract=outdated)
    )
    with pytest.raises(RuntimeError, match="agent_run_dispatch"):
        await worker.main()
    assert not serving.is_set()
