"""ARQ worker settings for durable Integration sync runs."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path, override=False)

from arq.connections import RedisSettings  # noqa: E402

from src.connectors.datasource.dependencies import init_registry  # noqa: E402
from src.connectors.datasource.run_repository import SyncRunRepository  # noqa: E402
from src.infra.supabase.client import SupabaseClient  # noqa: E402
from src.ingest.file.config import etl_config  # noqa: E402
from src.platform.integrations.engine import IntegrationEngine  # noqa: E402
from src.platform.integrations.jobs import execute_sync_run  # noqa: E402
from src.platform.integrations.repository import IntegrationRepository  # noqa: E402


logger = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    registry = init_registry()
    supabase = SupabaseClient()
    run_repo = SyncRunRepository(supabase)
    ctx["sync_run_repository"] = run_repo
    ctx["integration_engine"] = IntegrationEngine(
        registry=registry,
        repository=IntegrationRepository(supabase),
        run_repo=run_repo,
    )
    ctx["arq_queue_name"] = etl_config.sync_arq_queue_name
    logger.info("Sync ARQ worker startup complete")


async def shutdown(ctx: dict) -> None:
    logger.info("Sync ARQ worker shutdown")


class WorkerSettings:
    functions = [execute_sync_run]  # noqa: RUF012
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(etl_config.etl_redis_url)
    queue_name = etl_config.sync_arq_queue_name
    job_timeout = etl_config.sync_task_timeout
