"""HTTP boundary of the Project dashboard."""

import asyncio

from fastapi import APIRouter, Depends

from src.common_schemas import ApiResponse
from src.file_operations.dependencies import get_product_operation_adapter
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.infra.execution.runner import run_blocking
from src.infra.supabase.client import SupabaseClient
from src.platform.authorization.dependencies import AuthorizedProject, require_project_action
from src.platform.dashboard.resources import fetch_dashboard_resources
from src.platform.dashboard.schemas import DashboardProject, ResourceDashboard
from src.platform.dashboard.sections import compute_node_counts, fetch_tools, fetch_uploads
from src.platform.query_contract import strict_query
from src.shared_types.grants import ProjectAction

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get(
    "/{project_id}/dashboard/resources",
    response_model=ApiResponse[ResourceDashboard],
    dependencies=[strict_query()],
)
async def get_resource_dashboard(
    authorized: AuthorizedProject = Depends(require_project_action(ProjectAction.PROJECT_READ)),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project = authorized.project
    project_id = str(project.id)
    sb = SupabaseClient().client
    counts, resources, tools, uploads = await asyncio.gather(
        run_blocking(compute_node_counts, ops.for_grant(authorized.grant), project_id),
        run_blocking(fetch_dashboard_resources, sb, project_id),
        run_blocking(fetch_tools, sb, project_id),
        run_blocking(fetch_uploads, sb, project_id),
    )
    return ApiResponse.success(
        ResourceDashboard(
            project=DashboardProject(
                id=project_id, name=project.name, description=project.description
            ),
            nodes=counts,
            resources=resources,
            tools=tools,
            uploads=uploads,
        )
    )
