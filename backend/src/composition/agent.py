"""Connect the Agent to the modules that cannot import it."""

from __future__ import annotations

from src.platform.agent.config.surface import AgentSurface
from src.platform.agent.config.tool_bindings import AgentToolBindings
from src.platform.access.surface_kinds import register_surface_kind
from src.tool.surface_bindings import install_tool_surface_bindings


def install_agent() -> None:
    """Call once in the API process, the only one serving these routes."""
    register_surface_kind("agent", AgentSurface())
    install_tool_surface_bindings(AgentToolBindings())
