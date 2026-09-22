"""
Project Repository

Defines the data access interface and implementation for Project
"""

from abc import ABC, abstractmethod

from src.platform.project.models import Project


class ProjectRepositoryBase(ABC):
    """Abstract Project repository interface"""

    @abstractmethod
    def get_by_id(self, project_id: str) -> Project | None:
        """Get project by ID"""

    @abstractmethod
    def get_by_org_id(self, org_id: str) -> list[Project]:
        """Get project list by organization ID"""

    @abstractmethod
    def update(
        self,
        project_id: str,
        name: str | None,
        description: str | None,
        visibility: str | None = None,
        bound_git_branch: str | None = None,
    ) -> Project | None:
        """Update a project"""



class ProjectRepositorySupabase(ProjectRepositoryBase):
    """Supabase-based Project repository implementation"""

    def __init__(self, supabase_repo=None):
        """
        Initialize the repository

        Args:
            supabase_repo: Optional SupabaseRepository instance; creates a new one if not provided
        """
        if supabase_repo is None:
            from src.infra.supabase.dependencies import get_supabase_repository

            self._supabase_repo = get_supabase_repository()
        else:
            self._supabase_repo = supabase_repo

    def get_by_id(self, project_id: str) -> Project | None:
        """
        Get project by ID

        Args:
            project_id: Project ID

        Returns:
            Project object, or None if not found
        """
        project_response = self._supabase_repo.get_project(project_id)
        if project_response:
            return self._project_response_to_project(project_response)
        return None

    def get_by_org_id(self, org_id: str) -> list[Project]:
        """
        Get project list by organization ID

        Args:
            org_id: Organization ID

        Returns:
            List of Projects
        """
        projects_response = self._supabase_repo.get_projects(org_id=org_id)
        return [self._project_response_to_project(p) for p in projects_response]

    def rotate_share_token(self, project_id: str) -> Project | None:
        """Generate a new share token for ``project_id`` and persist it.

        Returns the updated ``Project`` (incl. the new token) or
        ``None`` if the row doesn't exist. Rotating is the "revoke all
        outstanding share links" mechanism — anyone holding the previous
        token gets a 404 on join after this call.
        """
        import secrets

        from src.platform.project.supabase_schemas import ProjectUpdate

        update_data = ProjectUpdate(share_token=secrets.token_urlsafe(24))
        project_response = self._supabase_repo.update_project(project_id, update_data)
        if project_response:
            return self._project_response_to_project(project_response)
        return None

    def find_by_share_token(self, token: str) -> Project | None:
        """Look up a project by its current share token. Returns None
        when no project has that token (either never issued or rotated
        out)."""
        from src.infra.supabase.dependencies import get_supabase_client

        client = get_supabase_client()
        resp = (
            client.table("projects")
            .select("*")
            .eq("share_token", token)
            .eq("lifecycle_status", "ready")
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        # Hydrate via the supabase_schemas response shape so the
        # converter has everything it expects.
        from src.platform.project.supabase_schemas import ProjectResponse

        return self._project_response_to_project(ProjectResponse(**resp.data[0]))

    def update(
        self,
        project_id: str,
        name: str | None,
        description: str | None,
        visibility: str | None = None,
        bound_git_branch: str | None = None,
        share_token: str | None = None,
    ) -> Project | None:
        """
        Update a project

        Args:
            project_id: Project ID
            name: Project name (optional, not updated if None)
            description: Project description (optional, not updated if None)
            visibility: Visibility (optional)
            bound_git_branch: Default git branch for new bindings (optional)
            share_token: Force-set share token (optional). Used by the
                share-link rotate flow; everyday update calls leave it
                alone.

        Returns:
            Updated Project object, or None if not found
        """
        from src.platform.project.supabase_schemas import ProjectUpdate

        update_data = ProjectUpdate(
            name=name,
            description=description,
        )
        if visibility is not None:
            update_data.visibility = visibility
        if bound_git_branch is not None:
            update_data.bound_git_branch = bound_git_branch
        if share_token is not None:
            update_data.share_token = share_token
        project_response = self._supabase_repo.update_project(project_id, update_data)
        if project_response:
            return self._project_response_to_project(project_response)
        return None

    def _project_response_to_project(self, project_response) -> Project:
        """
        Convert ProjectResponse to Project model

        Args:
            project_response: ProjectResponse object

        Returns:
            Project object
        """
        return Project(
            id=project_response.id,
            name=project_response.name,
            description=project_response.description,
            org_id=project_response.org_id,
            visibility=getattr(project_response, "visibility", "org"),
            bound_git_branch=getattr(project_response, "bound_git_branch", "main"),
            created_by=project_response.created_by,
            created_at=project_response.created_at,
            updated_at=getattr(project_response, "updated_at", None),
            share_token=getattr(project_response, "share_token", None),
        )
