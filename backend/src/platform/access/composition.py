"""Wire access adapters to the shared Workspace kernel."""

from fastapi import Depends, Request

from src.platform.workspace.service import WorkbenchService
from src.version_engine.bootstrap.dependencies import get_version_engine_container


async def get_workbench_service(request: Request, engine=Depends(get_version_engine_container)):
    service = getattr(request.app.state, "workbench_service", None)
    if service is None:
        service = build_workbench_service(engine=engine)
        request.app.state.workbench_service = service
    return service


def build_workbench_service(*, engine=None):
    from src.platform.access.adapters.git.workspace_transport import RunGitTransport
    from src.platform.billing.runtime import get_runtime_metering_service
    from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
    from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

    engine = engine or build_worker_version_engine_container()
    return WorkbenchService(
        RunGitTransport(engine.repo_manager), SandboxFilesystem(), get_runtime_metering_service()
    )
