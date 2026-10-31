"""Port through which Tools learn which surfaces bind them.

Bindings belong to the module that owns the surface, so Tools never import it.
The API process installs the implementation at startup.
"""

from __future__ import annotations

from typing import Any, Protocol


class ToolSurfaceBindings(Protocol):
    def surface_ids(self, tool_id: str) -> list[str]:
        """Return every surface bound to this tool."""

    def tools_on(self, surface_id: str) -> list[Any]:
        """Return the surface's tool bindings, each with a tool_id."""

    def mcp_surface_id(self, surface_id: str) -> str | None:
        """Return the id to invalidate if the surface is exposed over MCP."""


_installed: ToolSurfaceBindings | None = None


def install_tool_surface_bindings(bindings: ToolSurfaceBindings) -> None:
    global _installed
    _installed = bindings


def tool_surface_bindings() -> ToolSurfaceBindings:
    if _installed is None:
        raise RuntimeError("Tool surface bindings are not installed in this process")
    return _installed
