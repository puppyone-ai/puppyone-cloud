"""Project HTTP presentation helpers shared across application domains."""

from src.platform.authorization.models import ProjectGrant
from src.platform.project.models import Project
from src.platform.project.schemas import ProjectOut


def project_to_out(
    project: Project,
    grant: ProjectGrant,
    *,
    access_point_count: int | None = 0,
) -> ProjectOut:
    """Project metadata only; content remains in the Content API."""

    return ProjectOut(
        id=str(project.id),
        name=project.name,
        description=project.description,
        org_id=project.org_id,
        visibility=project.visibility,
        bound_git_branch=getattr(project, "bound_git_branch", "main"),
        updated_at=project.updated_at.isoformat() if project.updated_at else None,
        access_point_count=access_point_count,
        **grant.as_api_fields(),
    )
