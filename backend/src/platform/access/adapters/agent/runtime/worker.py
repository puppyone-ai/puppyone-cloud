"""Run with python -m src.platform.access.adapters.agent.runtime.worker.

The trusted worker process. ``WORKER_ROLES`` selects its modules:

- agent: claims Runs of its worker pool and runtime contract and supervises
  each in an isolated RunSupervisor (model proxy, approvals, publication,
  events, stop and recovery);
- sandbox: the only owner of machine lifecycle (src.platform.workspace.sandbox_worker).

The modules cooperate only through PostgreSQL, so one service may run both or
separate services of the same image may run one each. Durable SQL state is the
authority; this process holds only leases.

On SIGTERM/SIGINT it stops claiming, lets active Runs and lifecycle operations
continue for CLOUD_AGENT_SHUTDOWN_GRACE_SECONDS, then cancels and releases the
rest so a successor takes over without waiting for lease expiry. Compute is
never destroyed or paused by shutdown.
"""

import asyncio
import logging
import signal
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime

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
from src.platform.workspace.sandbox_worker import SandboxModule
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

logger = logging.getLogger(__name__)


_DEFAULT_OWNER = object()
ROLES = frozenset({"agent", "sandbox"})
STATS_INTERVAL_SECONDS = 60


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


def worker_roles(value=None) -> frozenset[str]:
    """Parse WORKER_ROLES (or an explicit value) into a non-empty subset of ROLES."""
    from src.config import settings

    raw = settings.WORKER_ROLES if value is None else value
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    roles = frozenset(part.strip() for part in parts if part.strip())
    if not roles or not roles <= ROLES:
        raise ValueError(f"WORKER_ROLES must name agent and/or sandbox, got {raw!r}")
    return roles


def queue_wait_seconds(run) -> float | None:
    """Seconds between admission and this claim; None when unknown."""
    with suppress(KeyError, TypeError, ValueError):
        created = datetime.fromisoformat(run["created_at"])
        return round((datetime.now(UTC) - created).total_seconds(), 3)
    return None


async def serve(*, workbench_owner=_DEFAULT_OWNER, stop=None, roles=None):
    from src.config import settings

    roles = worker_roles(roles)
    stop = stop or asyncio.Event()
    owns_workbench = workbench_owner is _DEFAULT_OWNER
    repository = RunRepository()
    engine = build_worker_version_engine_container(probe=True)
    if workbench_owner is _DEFAULT_OWNER:
        from src.platform.access.composition import build_workbench_service

        workbench_owner = build_workbench_service(engine=engine)
    identity = worker_identity(default_provider(), service="worker:" + "+".join(sorted(roles)))
    grace = settings.CLOUD_AGENT_SHUTDOWN_GRACE_SECONDS
    logger.info(
        "cloud_agent_worker_started",
        extra={"agent_dispatch": {**identity.claim(), "roles": sorted(roles)}},
    )
    concurrency = start_process_concurrency()
    modules = []
    if "sandbox" in roles:
        sandbox = SandboxModule(
            workbench_owner,
            pool=identity.pool,
            passes=[
                (
                    "session_workspace_retirement",
                    lambda: reap_workspaces(repository, PiWorker, pool=identity.pool),
                )
            ],
        )
        modules.append(asyncio.create_task(sandbox.serve(stop=stop, grace=grace)))
    if "agent" in roles:
        agent = serve_agent(
            repository, engine, workbench_owner, identity=identity, stop=stop, grace=grace
        )
        modules.append(asyncio.create_task(agent))
    try:
        await asyncio.gather(*modules)
    finally:
        for task in modules:
            task.cancel()
        await asyncio.gather(*modules, return_exceptions=True)
        try:
            if owns_workbench:
                await workbench_owner.close()
        finally:
            await concurrency.stop()


async def serve_agent(repository, engine, workbench_owner, *, identity, stop, grace):
    """Agent module: claim and supervise Runs of this pool until ``stop``."""
    from src.config import settings

    admission = Admission(repository)
    filesystem = SandboxFilesystem()
    publication = Publication(RunGitTransport(engine.repo_manager), filesystem)
    inference = get_inference_service(get_billing_gateway(), get_provider_registry())
    billing = get_runtime_metering_service()
    bound_tools = build_bound_tools(admission, engine)
    claim_as = identity.claim()
    active = {}  # task -> claimed run
    shared_first = False
    task_repository = SharedTaskRepository(repository.client) if workbench_owner else None
    counters = Counter()
    outcomes = Counter()
    waits = []

    def dispatch_fields(run, **extra):
        return {
            "run_id": run["id"],
            "fence": run.get("fence"),
            "worker_id": identity.id,
            "pool": identity.pool,
            **extra,
        }

    async def supervise(run):
        supervisor = None
        try:
            supervisor = RunSupervisor(
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
            )
            await supervisor.run_claim(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            counters["supervisor_failed"] += 1
            # The durable lease allows another owner to repair a failed cleanup.
            logger.exception(
                "cloud_agent_supervisor_failed", extra={"agent_dispatch": dispatch_fields(run)}
            )
        finally:
            outcome = getattr(supervisor, "outcome", None)
            if outcome:
                outcomes[f"{outcome['state']}:{outcome['code']}"] += 1

    def report():
        stats = {
            "module": "agent",
            "pool": identity.pool,
            "worker_id": identity.id,
            "active_runs": len(active),
            "capacity": settings.CLOUD_AGENT_CONCURRENCY,
            "claimed": counters["claimed"],
            "handed_off": counters["handed_off"],
            "supervisor_failed": counters["supervisor_failed"],
            "claim_errors": counters["claim_errors"],
            "outcomes": dict(outcomes),
            "queue_wait_max_seconds": max(waits, default=None),
        }
        counters.clear()
        outcomes.clear()
        waits.clear()
        logger.info("agent_worker_stats", extra={"worker_stats": stats})
        return stats

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
                counters["handed_off"] += 1
                logger.info(
                    "cloud_agent_run_handed_off", extra={"agent_dispatch": dispatch_fields(run)}
                )
            except Exception:
                # Already finished or superseded; lease expiry remains the fallback.
                logger.warning(
                    "cloud_agent_handoff_skipped", extra={"agent_dispatch": dispatch_fields(run)}
                )

    stats_due = time.monotonic() + STATS_INTERVAL_SECONDS
    try:
        while not stop.is_set():
            if time.monotonic() >= stats_due:
                report()
                stats_due = time.monotonic() + STATS_INTERVAL_SECONDS
            if len(active) >= settings.CLOUD_AGENT_CONCURRENCY:
                stopping = asyncio.create_task(stop.wait())
                try:
                    await asyncio.wait(
                        [*active, stopping],
                        timeout=STATS_INTERVAL_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
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
                wait = queue_wait_seconds(run)
                counters["claimed"] += 1
                if wait is not None:
                    waits.append(wait)
                logger.info(
                    "cloud_agent_run_claimed",
                    extra={
                        "agent_dispatch": dispatch_fields(
                            run,
                            queue_wait_seconds=wait,
                            dispatch_attempts=run.get("dispatch_attempts", 0),
                            workbench=bool(run.get("workbench_id")),
                        )
                    },
                )
                task = asyncio.create_task(supervise(run))
                active[task] = run
                task.add_done_callback(lambda done: active.pop(done, None))
            except asyncio.CancelledError:
                raise
            except Exception:
                counters["claim_errors"] += 1
                logger.exception("cloud_agent_dispatch_failed")
                await _until(stop, 2)
        logger.info(
            "cloud_agent_worker_stopping", extra={"agent_dispatch": {"worker_id": identity.id}}
        )
        await hand_off()
        report()
    finally:
        pending = list(active)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


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
