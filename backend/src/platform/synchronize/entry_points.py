"""What Synchronize contributes to a Project's entry point count."""

from __future__ import annotations

from src.infra.supabase.client import SupabaseClient
from src.platform.synchronize.repository import SynchronizeRepository


def count_synchronize_entry_points(project_ids: list[str]) -> dict[str, int]:
    return SynchronizeRepository(SupabaseClient()).count_by_projects(project_ids)
