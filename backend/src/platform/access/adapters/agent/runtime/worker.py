"""Run with python -m src.platform.access.adapters.agent.runtime.worker.

The trusted host supervisor. It claims Runs of its own worker pool and runtime
contract, supervises each in an isolated RunSupervisor, executes workspace
operations of its pool and reclaims idle compute. Durable SQL state is the
authority; this process holds only leases.

On SIGTERM/SIGINT it stops claiming, lets active Runs continue for
CLOUD_AGENT_SHUTDOWN_GRACE_SECONDS, then cancels and releases the rest so a
successor takes over without waiting for lease expiry. Compute is never
destroyed or paused by shutdown.
"""

import asyncio
import logging
import signal
from contextlib import suppress

from src.infra.execution.process import start_process_concurrency
from src.infra.execution.runner import run_blocking
from src.platform.access.adapters.agent.runtime.control.admission import Admission
from src.platform.access.adapters.agent.runtime.control.publication import Publication
from src.platform.access.adapters.agent.runtime.control.runner import RunSupervisor
from src.platform.access.adapters.agent.runtime.control.shared_tasks import SharedTasks
from src.platform.access.adapters.agent.runtime.control.workspace import (
    SessionWorkspace,
    reap_workspaces,
)
from src.platform.access.adapters.agent.runtime.dispatch import worker_identity
from src.platform.access.adapters.agent.runtime.execution.sandbox import SandboxExecution
from src.platform.access.adapters.agent.runtime.execution.tools import BoundTools
from src.platform.access.adapters.agent.runtime.loop.pi import PiLoop
from src.platform.access.adapters.agent.runtime.persistence.commands import RunRepository
from src.platform.access.adapters.agent.runtime.persistence.shared_tasks import SharedTaskRepository
from src.platform.access.adapters.git.workspace_transport import RunGitTransport
from src.platform.billing.gateway import get_billing_gateway
from src.platform.billing.runtime import get_runtime_metering_service
from src.platform.managed_ai.dependencies import get_inference_service, get_provider_registry
from src.platform.sandbox import default_provider, require_binding
from src.platform.workspace.execution.pi_worker import PiWorker
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

logger = logging.getLogger(__name__)


_DEFAULT_OWNER = object()


def build_bound_tools(admission, engine):
    from src.infra.llm.dependencies import get_embedding_service
    from src.infra.search.dependencies import get_search_service
    from src.infra.supabase.dependencies import get_supabase_client
    from src.infra.turbopuffer.dependencies import get_turbopuffer_search_service

    search = get_search_service(admission.authorization, engine.product_operations(),
                                get_supabase_client(), get_embedding_service(),
                                get_turbopuffer_search_service())
    return BoundTools(admission, search)


async def _until(stop, seconds):
    """Sleep, returning early when the worker is asked to stop."""
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def serve(*, workbench_owner=_DEFAULT_OWNER, stop=None):
    from src.config import settings

    stop = stop or asyncio.Event()
    owns_workbench = workbench_owner is _DEFAULT_OWNER
    repository = RunRepository()
    admission = Admission(repository)
    engine = build_worker_version_engine_container(probe=True)
    if workbench_owner is _DEFAULT_OWNER:
        from src.platform.access.composition import build_workbench_service

        workbench_owner = build_workbench_service(engine=engine)
    filesystem = SandboxFilesystem()
    publication = Publication(RunGitTransport(engine.repo_manager), filesystem)
    inference = get_inference_service(get_billing_gateway(), get_provider_registry())
    billing = get_runtime_metering_service()
    bound_tools = build_bound_tools(admission, engine)
    identity = worker_identity(default_provider())
    claim_as = identity.claim()
    grace = settings.CLOUD_AGENT_SHUTDOWN_GRACE_SECONDS
    active = {}  # task -> claimed run
    shared_first = False
    task_repository = SharedTaskRepository(repository.client) if workbench_owner else None
    logger.info("cloud_agent_worker_started", extra={"agent_worker": claim_as})

    async def supervise(run):
        try:
            await RunSupervisor(
                repository,
                admission,
                publication,
                inference,
                billing,
                bound_tools=bound_tools,
                loop=PiLoop(),
                execution=SandboxExecution(),
                filesystem=filesystem,
                worker_factory=PiWorker,
                worker_lifecycle=PiWorker,
                workspace=None
                if run.get("workbench_id")
                else SessionWorkspace(repository, filesystem),
                shared_tasks=(
                    SharedTasks(task_repository, workbench_owner)
                    if run.get("workbench_id")
                    else None
                ),
                capability=require_binding,
            ).run_claim(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The durable lease allows another owner to repair a failed cleanup.
            logger.exception(
                "cloud_agent_supervisor_failed",
                extra={"run_id": run["id"], "worker_id": identity.id},
            )

    async def retire_idle():
        while True:
            try:
                await reap_workspaces(repository, PiWorker, pool=identity.pool)
                if workbench_owner:
                    await workbench_owner.reap()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cloud_agent_workspace_reaper_failed")
            await asyncio.sleep(30)

    async def hand_off():
        """Stop gracefully: finish within the grace period, then release leases."""
        if active:
            await asyncio.wait(list(active), timeout=grace)
        remaining = dict(active)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        for run in remaining.values():
            try:
                await run_blocking(repository.release_execution, run, reason="worker_shutdown")
                logger.info(
                    "cloud_agent_run_handed_off",
                    extra={"run_id": run["id"], "worker_id": identity.id},
                )
            except Exception:
                # Already finished or superseded; lease expiry remains the fallback.
                logger.warning(
                    "cloud_agent_handoff_skipped",
                    extra={"run_id": run["id"], "worker_id": identity.id},
                )

    concurrency = start_process_concurrency()
    lifecycle = (
        asyncio.create_task(workbench_owner.lifecycle.serve(stop=stop, grace=grace))
        if workbench_owner
        else None
    )
    reaper = asyncio.create_task(retire_idle())
    try:
        while not stop.is_set():
            if len(active) >= settings.CLOUD_AGENT_CONCURRENCY:
                stopping = asyncio.create_task(stop.wait())
                try:
                    await asyncio.wait([*active, stopping], return_when=asyncio.FIRST_COMPLETED)
                finally:
                    stopping.cancel()
                continue
            try:
                claims = [repository.claim_run]
                if workbench_owner:
                    claims.append(repository.claim_workbench_run)
                    if shared_first:
                        claims.reverse()
                    shared_first = not shared_first
                run = None
                for claim in claims:
                    run = await run_blocking(claim, worker=claim_as)
                    if run is not None:
                        break
                if run is None:
                    await _until(stop, 1)
                    continue
                logger.info(
                    "cloud_agent_run_claimed",
                    extra={
                        "run_id": run["id"],
                        "fence": run.get("fence"),
                        "worker_id": identity.id,
                        "worker_pool": identity.pool,
                    },
                )
                task = asyncio.create_task(supervise(run))
                active[task] = run
                task.add_done_callback(lambda done: active.pop(done, None))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cloud_agent_dispatch_failed")
                await _until(stop, 2)
        logger.info("cloud_agent_worker_stopping", extra={"worker_id": identity.id})
        reaper.cancel()
        await asyncio.gather(
            hand_off(), *([lifecycle] if lifecycle else []), return_exceptions=True
        )
    finally:
        pending = [*active, reaper, *([lifecycle] if lifecycle else [])]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        try:
            if owns_workbench:
                await workbench_owner.close()
        finally:
            await concurrency.stop()


async def main():
    # Exit before claiming when the database lacks routed dispatch, instead of
    # retrying a missing RPC in the claim loop.
    await run_blocking(RunRepository().verify_dispatch_contract)
    stop = asyncio.Event()
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()

    def request_stop():
        if stop.is_set():
            current.cancel()  # A second signal skips the grace period.
        stop.set()

    for value in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(value, request_stop)
    await serve(stop=stop)


if __name__ == "__main__":
    from src.platform.workspace.execution.preflight import main as preflight
    from src.utils.logging_setup import setup_logging

    setup_logging()
    preflight()
    asyncio.run(main())
