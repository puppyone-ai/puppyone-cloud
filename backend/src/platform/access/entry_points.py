"""What Access contributes to a Project's entry point count."""

from __future__ import annotations

from src.infra.supabase.dependencies import get_supabase_client
from src.platform.access.surface_repository import AccessSurfaceRepository

# CLI, Agent, Filesystem and Git Remote are built-in methods, not user creations.
_USER_CREATED_KINDS = ["mcp", "sandbox"]


def count_access_entry_points(project_ids: list[str]) -> dict[str, int]:
    return AccessSurfaceRepository(get_supabase_client()).count_by_projects_and_kinds(
        project_ids, _USER_CREATED_KINDS
    )
