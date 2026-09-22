"""Version Engine write boundary for Integration syncs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.platform.integrations.paths import IntegrationWritePlan


@dataclass(frozen=True)
class IntegrationWriteOutcome:
    commit_id: str


class IntegrationVersionWritePort(Protocol):
    async def write_plan(
        self,
        *,
        project_id: str,
        plan: IntegrationWritePlan,
        actor: str,
    ) -> IntegrationWriteOutcome:
        """Commit an Integration write plan through the Version Engine."""


class VersionEngineWritePort:
    async def write_plan(
        self,
        *,
        project_id: str,
        plan: IntegrationWritePlan,
        actor: str,
    ) -> IntegrationWriteOutcome:
        from src.platform.project.write_lease import build_leased_worker_write_commands

        commands = build_leased_worker_write_commands()
        if len(plan.files) == 1 and not plan.deleted:
            file_path, content = next(iter(plan.files.items()))
            outcome = await commands.write_bytes(
                project_id,
                file_path,
                content,
                actor=actor,
                message=plan.message,
                source_channel="sync",
            )
        else:
            outcome = await commands.bulk_write(
                project_id,
                plan.files,
                actor=actor,
                deleted=plan.deleted,
                message=plan.message,
                source_channel="sync",
            )
        return IntegrationWriteOutcome(commit_id=outcome.result.commit_id)
