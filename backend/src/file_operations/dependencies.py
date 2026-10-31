"""FastAPI providers for Product file operations."""

from __future__ import annotations

from fastapi import Depends

from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.engine_dependencies import get_version_engine_container
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.version_engine.bootstrap.container import VersionEngineContainer


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
