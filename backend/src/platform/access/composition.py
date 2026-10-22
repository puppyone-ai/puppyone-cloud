"""Wire access adapters to the shared Workspace kernel."""

from src.platform.workspace.service import WorkbenchService


def build_workbench_service(*, engine=None):
    from src.platform.access.adapters.git.workspace_transport import RunGitTransport
    from src.platform.billing.runtime import get_runtime_metering_service
    from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
    from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

    engine = engine or build_worker_version_engine_container()
    return WorkbenchService(
        RunGitTransport(engine.repo_manager), SandboxFilesystem(), get_runtime_metering_service()
    )
