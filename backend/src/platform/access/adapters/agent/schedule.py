"""Scheduled Agents submit the same durable runs as interactive clients.

APScheduler runs this job in its thread pool; every step is a synchronous
call, so no event loop is created. Automation is not offered, so nothing calls
`load_scheduled_agents` at startup.
"""

import logging
from uuid import uuid4

from apscheduler.job import Job

from src.infra.execution.runner import run_blocking
from src.infra.scheduler.service import THREAD_POOL, get_scheduler_service
from src.platform.access.adapters.agent.runtime.control.models import SubmitRun
from src.platform.access.surface_repository import AccessSurfaceRepository
from src.platform.authorization.service import redacted_project_ref
from src.shared_types.grants import ProjectAction
from src.utils.logger import log_error, log_info, log_warning

logger = logging.getLogger(__name__)


def execute_agent_task(agent_id: str) -> dict:
    from src.infra.supabase.client import SupabaseClient
    from src.platform.authorization.factory import build_authorization_service

    client = SupabaseClient().client
    surface = AccessSurfaceRepository(client).get_agent_with_project(agent_id)
    if surface is None:
        return {"status": "failed", "error": "Agent not found"}
    if surface.get("status", "active") != "active":
        return {"status": "skipped", "reason": "agent_paused"}
    user_id = surface.get("created_by") or (surface.get("project") or {}).get("created_by")
    if not user_id:
        return {"status": "failed", "error": "Agent has no associated user"}
    try:
        authorized = build_authorization_service().allows(
            surface["project_id"],
            user_id,
            ProjectAction.AGENT_RUN,
        )
    except Exception:
        logger.warning(
            "scheduled_agent_principal_check_failed",
            extra={"project_ref": redacted_project_ref(surface["project_id"])},
        )
        return {"status": "failed", "error": "Principal access check failed"}
    if not authorized:
        return {"status": "failed", "error": "principal_invalid"}
    request_id = str(uuid4())
    config = surface.get("config") or {}
    try:
        from src.platform.access.adapters.agent.dependencies import get_agent_service

        service = get_agent_service()
        result = service.submit(
            user_id,
            SubmitRun(
                project_id=surface["project_id"],
                agent_id=agent_id,
                request_id=request_id,
                prompt=config.get("task_content") or "",
            ),
            interactive=False,
        )
        # The run is the durable execution log. A scheduler process may exit
        # immediately after this receipt without interrupting the Agent.
        return {"status": "accepted", "run_id": result["id"], "session_id": result["session_id"]}
    except Exception:
        logger.exception("scheduled_agent_submission_failed", extra={"agent_id": agent_id})
        return {"status": "failed", "error": "Agent submission failed"}


def add_agent_job(agent_id: str, trigger_config: dict, agent_name: str = "") -> Job | None:
    """
    Add a new agent job to the scheduler.

    Args:
        agent_id: The agent's unique ID (used as job_id)
        trigger_config: Configuration containing schedule info
            - schedule: cron expression (e.g., "0 9 * * *")
            - timezone: optional timezone override
            - date: ISO date for one-time execution
            - repeat_type: 'once', 'daily', 'weekly'
        agent_name: Human-readable name for logging
    """
    scheduler = get_scheduler_service()
    if not scheduler.running:
        log_warning(f"Scheduler not running, skipping job for agent {agent_id}")
        return None

    # Remove existing job if any
    remove_agent_job(agent_id)

    # Parse trigger configuration
    trigger = scheduler.parse_trigger(trigger_config)
    if not trigger:
        log_warning(f"Invalid trigger config for agent {agent_id}: {trigger_config}")
        return None

    # Add the job
    job = scheduler.add_job(
        execute_agent_task,
        trigger,
        job_id=agent_id,
        name=f"Agent: {agent_name or agent_id}",
        args=[agent_id],
        executor=THREAD_POOL,
    )

    next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "N/A"
    log_info(f"📅 Added job for agent '{agent_name}' ({agent_id}), next run: {next_run}")

    return job


def remove_agent_job(agent_id: str) -> bool:
    """Remove an agent job from the scheduler."""
    removed = get_scheduler_service().remove_job(agent_id)
    if removed:
        log_info(f"🗑️  Removed job for agent {agent_id}")
    return removed


def _scheduled_agent_rows() -> list[dict]:
    return [
        row
        for row in AccessSurfaceRepository().list_all(kind="agent", status="active")
        if (row.get("config") or {}).get("type") == "schedule"
        and ((row.get("config") or {}).get("trigger") or {}).get("type") in {"cron", "scheduled"}
    ]


async def load_scheduled_agents() -> None:
    """Load all schedule agents from database and register jobs."""
    if not get_scheduler_service().scheduler:
        return

    try:
        # Reads the database off the event loop so startup never blocks it.
        agents = await run_blocking(_scheduled_agent_rows)
        log_info(f"📋 Found {len(agents)} schedule agents to load")

        for agent in agents:
            config = agent.get("config") or {}
            trigger = config.get("trigger") or {}
            add_agent_job(
                agent_id=agent["id"],
                trigger_config=trigger.get("config") or trigger,
                agent_name=config.get("name", "Unknown"),
            )

        log_info(f"✅ Loaded {len(agents)} agent jobs")

    except Exception as e:
        log_error(f"❌ Failed to load scheduled agents: {e}")
