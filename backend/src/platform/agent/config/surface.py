"""The Agent as an Access surface kind."""

from __future__ import annotations

from src.platform.agent.config.repository import AgentRepository
from src.platform.agent.config.schemas import AgentBashCreate
from src.platform.agent.config.service import AgentConfigService


class AgentSurface:
    def create(self, payload):
        service = AgentConfigService(repository=AgentRepository())

        bash_accesses = []
        if payload.accesses:
            bash_accesses = [
                AgentBashCreate(
                    path=a["path"],
                    readonly=a.get("readonly", a.get("terminal_readonly", True)),
                )
                for a in payload.accesses
            ]

        cfg = payload.config
        return service.create_agent(
            project_id=payload.project_id,
            name=payload.name or cfg.get("name", "Chat Agent"),
            icon=cfg.get("icon", "✨"),
            type=cfg.get("type", "chat"),
            description=cfg.get("description"),
            bash_accesses=bash_accesses,
            trigger_type=cfg.get("trigger_type", "manual"),
            trigger_config=cfg.get("trigger_config"),
            task_content=cfg.get("task_content"),
            task_path=cfg.get("task_path"),
            external_config=cfg.get("external_config"),
        )

    def regenerate_key(self, surface_id, client):
        return AgentRepository(client).regenerate_mcp_api_key(surface_id)
