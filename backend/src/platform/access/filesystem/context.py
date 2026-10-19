"""Runtime context for scoped filesystem commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ScopedFsContext:
    """Resolved scope facts for one MCP filesystem runtime."""

    api_key: str
    endpoint_id: str
    endpoint_name: str
    project_id: str
    user_id: str
    scope_id: str
    scope_path: str = ""
    mode: Literal["ro", "rw"] = "ro"
    exclude: list[str] = field(default_factory=list)
    allowed_tools: frozenset[str] | None = None
    channel: str = "mcp"

    @property
    def writable(self) -> bool:
        return self.mode == "rw"

    @property
    def actor(self) -> str:
        return self.user_id or f"mcp:{self.endpoint_id}"
