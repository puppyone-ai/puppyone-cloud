"""Run with python -m src.platform.access.adapters.agent.runtime.worker."""

import asyncio
import logging
from uuid import uuid4

from src.platform.access.adapters.agent.runtime.control.admission import Admission
from src.platform.access.adapters.agent.runtime.control.publication import Publication
from src.platform.access.adapters.agent.runtime.control.runner import RunSupervisor
from src.platform.access.adapters.agent.runtime.control.shared_tasks import SharedTasks
from src.platform.access.adapters.agent.runtime.control.workspace import (
    SessionWorkspace,
    reap_workspaces,
)
from src.platform.access.adapters.agent.runtime.execution.sandbox import SandboxExecution
from src.platform.access.adapters.agent.runtime.execution.tools import BoundTools
from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem
from src.platform.access.adapters.agent.runtime.loop.pi import PiLoop
from src.platform.access.adapters.agent.runtime.persistence.commands import RunRepository
from src.platform.access.adapters.agent.runtime.persistence.shared_tasks import SharedTaskRepository
from src.platform.billing.gateway import get_billing_gateway
from src.platform.billing.runtime import get_runtime_metering_service
from src.platform.managed_ai.dependencies import get_inference_service, get_provider_registry
from src.platform.scope_sandbox.execution.pi_worker import PiWorker
from src.version_engine.adapters.git.run_transport import RunGitTransport
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

logger = logging.getLogger(__name__)


async def serve(*, workbench_owner=None):
    from src.config import settings

    repository = RunRepository()
    admission = Admission(repository)
    engine = build_worker_version_engine_container(probe=True)
    filesystem = SandboxFilesystem()
    publication = Publication(RunGitTransport(engine.repo_manager), filesystem)
    inference = get_inference_service(get_billing_gateway(), get_provider_registry())
    billing = get_runtime_metering_service()
    bound_tools = BoundTools(admission)
    worker_id = str(uuid4())
    active = set()
    shared_first = False
    task_repository = SharedTaskRepository(repository.client) if workbench_owner else None

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
                workspace=None if run.get("workbench_id") else SessionWorkspace(repository, filesystem),
                shared_tasks=(
                    SharedTasks(task_repository, workbench_owner)
                    if run.get("workbench_id")
                    else None
                ),
            ).run_claim(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The durable lease allows another owner to repair a failed cleanup.
            logger.exception("cloud_agent_supervisor_failed", extra={"run_id": run["id"]})

    async def retire_idle():
        while True:
            try:
                await reap_workspaces(repository, PiWorker)
                if workbench_owner:
                    await workbench_owner.reap()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cloud_agent_workspace_reaper_failed")
            await asyncio.sleep(30)

    reaper = asyncio.create_task(retire_idle())
    try:
        while True:
            if len(active) >= settings.CLOUD_AGENT_CONCURRENCY:
                await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
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
                    run = await asyncio.to_thread(claim, worker=worker_id)
                    if run is not None:
                        break
                if run is None:
                    await asyncio.sleep(1)
                    continue
                task = asyncio.create_task(supervise(run))
                active.add(task)
                task.add_done_callback(active.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cloud_agent_dispatch_failed")
                await asyncio.sleep(2)
    finally:
        pending = [*active, reaper]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    from src.utils.logging_setup import setup_logging

    setup_logging()
    asyncio.run(serve())
