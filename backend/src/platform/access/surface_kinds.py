"""Surface kinds owned by other modules.

Access stores and dispatches every kind of surface. A module that owns one
registers how to create it, so Access never imports that module.
"""

from __future__ import annotations

from typing import Any, Protocol


class ManagedSurface(Protocol):
    def create(self, payload: Any) -> Any:
        """Create the surface and return an object with id, name and mcp_api_key."""

    def regenerate_key(self, surface_id: str, client: Any) -> str | None:
        """Replace the surface's bearer key, returning the new one if it exists."""


_kinds: dict[str, ManagedSurface] = {}


def register_surface_kind(kind: str, surface: ManagedSurface) -> None:
    _kinds[kind] = surface


def surface_kind(kind: str) -> ManagedSurface:
    if kind not in _kinds:
        raise RuntimeError(f"Access surface kind {kind!r} is not registered in this process")
    return _kinds[kind]
