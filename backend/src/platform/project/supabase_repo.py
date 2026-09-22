"""
Project Data Access Layer

Provides CRUD operations for the project table.
"""

from supabase import Client

from src.infra.supabase.exceptions import handle_supabase_error
from src.platform.project.supabase_schemas import (
    ProjectResponse,
    ProjectUpdate,
)


class ProjectRepository:
    """Project data access repository"""

    def __init__(self, client: Client):
        self._client = client

    def get_by_id(self, project_id: str) -> ProjectResponse | None:
        response = (
            self._client.table("projects")
            .select("*")
            .eq("id", project_id)
            .eq("lifecycle_status", "ready")
            .execute()
        )
        if response.data:
            return ProjectResponse(**response.data[0])
        return None

    def get_list(
        self,
        skip: int = 0,
        limit: int = 100,
        org_id: str | None = None,
        name: str | None = None,
    ) -> list[ProjectResponse]:
        query = self._client.table("projects").select("*").eq("lifecycle_status", "ready")

        if org_id is not None:
            query = query.eq("org_id", org_id)

        if name:
            query = query.eq("name", name)

        response = query.range(skip, skip + limit - 1).execute()
        return [ProjectResponse(**item) for item in response.data]

    def update(self, project_id: str, project_data: ProjectUpdate) -> ProjectResponse | None:
        try:
            data = project_data.model_dump(exclude_none=True)
            if not data:
                return self.get_by_id(project_id)

            data.pop("id", None)
            data.pop("created_at", None)

            response = (
                self._client.table("projects")
                .update(data)
                .eq("id", project_id)
                .eq("lifecycle_status", "ready")
                .execute()
            )
            if response.data:
                return ProjectResponse(**response.data[0])
            return None
        except Exception as e:
            raise handle_supabase_error(e, "update project")
