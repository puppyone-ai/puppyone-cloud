"""ARQ jobs for the GitHub integration.

The webhook used to run ``import_branch`` in-process via a fire-and-forget
``asyncio`` task — fine for a single API box, but the import dies with the
process on a deploy/crash and competes with request handling. These jobs move
the actual import onto the durable ``imports`` worker queue; the webhook only
validates + enqueues + acks (well within GitHub's 5s budget).
"""

from __future__ import annotations

from typing import Any, Optional

from src.repo.github_integration.importer import import_branch
from src.repo.github_integration.repository import GithubIntegrationRepository
from src.utils.logger import log_error, log_info


async def execute_github_import(
    ctx: dict,
    integration_id: str,
    *,
    branch: Optional[str] = None,
    force: bool = False,
    triggered_by: str = "webhook",
) -> dict[str, Any]:
    """Run a GitHub branch import for one integration on the worker.

    Re-fetches the integration by id (rather than threading a stale dict through
    Redis) and delegates to ``import_branch``, which records its own
    ``github_sync_log`` row and watermark. ``import_branch`` already converts
    expected failures into a recorded ``failed``/``conflict`` result, so this
    rarely raises; an unexpected raise propagates so ARQ marks the job failed.
    """
    integration = await GithubIntegrationRepository().get_by_id(integration_id)
    if not integration:
        log_error(f"[github-import-job] integration {integration_id} not found; skipping")
        return {"status": "skipped", "reason": "integration_not_found",
                "integration_id": integration_id}

    result = await import_branch(
        integration, branch=branch, force=force, triggered_by=triggered_by,
    )
    log_info(
        f"[github-import-job] integration={integration_id} "
        f"branch={branch or 'default'} status={result.status}"
    )
    return {"status": result.status, "integration_id": integration_id,
            "git_sha": result.git_sha}
