"""Tell Project which modules own its entry points."""

from __future__ import annotations

from src.platform.access.entry_points import count_access_entry_points
from src.platform.project.entry_points import register_entry_point_counter
from src.platform.synchronize.entry_points import count_synchronize_entry_points


def install_project_entry_points() -> None:
    """Call once in the API process, the only one serving Project routes."""
    register_entry_point_counter("synchronize", count_synchronize_entry_points)
    register_entry_point_counter("access", count_access_entry_points)
