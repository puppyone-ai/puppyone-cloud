"""FastAPI providers for the Version Engine and its Product file operations."""

from __future__ import annotations

from fastapi import Depends, Request

from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.version_engine.bootstrap.container import (
    VersionEngineContainer,
    build_version_engine_container,
)
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager
from src.version_engine.read.admin import VersionAdminService
from src.version_engine.read.history_graph import HistoryGraphService
from src.version_engine.write_engine.engine import VersionWriteEngine


def get_version_engine_container(request: Request) -> VersionEngineContainer:
    """Return the app-scoped container installed by FastAPI lifespan."""

    container = getattr(request.app.state, "version_engine", None)
    if container is None:
        container = build_version_engine_container()
        request.app.state.version_engine = container
    return container


def get_repo_manager(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> VersionRepoManager:
    return container.repo_manager


def get_version_admin_service(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> VersionAdminService:
    return container.admin_service()


def get_history_graph_service(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> HistoryGraphService:
    return container.history_graph()


def get_version_write_engine(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> VersionWriteEngine:
    """Native initialization facade; content writes use Product commands."""

    return container.write_engine()


def product_operations(container: VersionEngineContainer) -> ProductOperationAdapter:
    return ProductOperationAdapter(container.repo_manager)


def write_commands(container: VersionEngineContainer) -> VersionWriteCommandService:
    return VersionWriteCommandService(product_operations(container))


def get_product_operation_adapter(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> ProductOperationAdapter:
    return product_operations(container)


def get_version_write_command_service(
    container: VersionEngineContainer = Depends(get_version_engine_container),
) -> VersionWriteCommandService:
    return write_commands(container)
