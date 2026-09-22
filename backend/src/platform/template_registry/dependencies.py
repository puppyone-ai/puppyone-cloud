"""FastAPI dependency wiring for Template Registry services."""

from __future__ import annotations

from fastapi import Depends

from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.entitlements.service import EntitlementService
from src.platform.project.control_plane import ProjectControlPlaneService
from src.platform.project.control_plane_dependencies import get_project_control_plane_service
from src.version_engine.adapters.product.commands import VersionWriteCommandService
from src.version_engine.bootstrap.dependencies import (
    get_version_write_command_service,
    get_version_write_engine,
)
from src.version_engine.write_engine.engine import VersionWriteEngine

from .config import TemplateRegistrySettings, template_registry_settings
from .instantiation import TemplateInstantiationService
from .provider import DisabledTemplateRegistryProvider
from .providers import BuiltinTemplateRegistryProvider, RemoteTemplateRegistryProvider
from .service import TemplateRegistryService

_registry_service: TemplateRegistryService | None = None


def build_template_registry_service(
    settings: TemplateRegistrySettings,
) -> TemplateRegistryService:
    if settings.TEMPLATE_REGISTRY_MODE == "builtin":
        provider = BuiltinTemplateRegistryProvider(settings)
    elif settings.TEMPLATE_REGISTRY_MODE == "remote":
        provider = RemoteTemplateRegistryProvider(settings)
    else:
        provider = DisabledTemplateRegistryProvider()
    return TemplateRegistryService(provider=provider, settings=settings)


def get_template_registry_service() -> TemplateRegistryService:
    global _registry_service
    if _registry_service is None:
        _registry_service = build_template_registry_service(template_registry_settings)
    return _registry_service


def get_template_instantiation_service(
    registry: TemplateRegistryService = Depends(get_template_registry_service),
    control_plane: ProjectControlPlaneService = Depends(get_project_control_plane_service),
    entitlements: EntitlementService = Depends(get_entitlement_service),
    version_engine: VersionWriteEngine = Depends(get_version_write_engine),
    write_commands: VersionWriteCommandService = Depends(get_version_write_command_service),
) -> TemplateInstantiationService:
    return TemplateInstantiationService(
        registry=registry,
        control_plane=control_plane,
        entitlements=entitlements,
        version_engine=version_engine,
        write_commands=write_commands,
    )
