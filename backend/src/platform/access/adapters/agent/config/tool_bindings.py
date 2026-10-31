"""Agent tool bindings as seen by the Tool module."""

from __future__ import annotations

from src.platform.access.adapters.agent.config.repository import AgentRepository


class AgentToolBindings:
    def surface_ids(self, tool_id):
        return AgentRepository().list_access_surface_ids_by_tool(tool_id)

    def tools_on(self, surface_id):
        return AgentRepository().get_tools_by_agent_id(surface_id)

    def mcp_surface_id(self, surface_id):
        agent = AgentRepository().get_by_id(surface_id)
        return agent.id if agent and agent.mcp_enabled else None
