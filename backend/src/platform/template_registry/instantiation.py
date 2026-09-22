"""Create an independent Project from one verified immutable release."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.platform.entitlements.service import EntitlementService
from src.platform.project.control_plane import (
    ProjectControlPlaneService,
    ProjectCreationReplay,
)
from src.platform.project.models import Project
from src.platform.project.orchestration import create_project_with_tree
from src.version_engine.adapters.product.commands import VersionWriteCommandService
from src.version_engine.write_engine.engine import VersionWriteEngine

from .service import TemplateRegistryService

_TEMPLATE_REQUEST_KIND = "template-instantiation-request"
_TEMPLATE_RESULT_KIND = "template-instantiation"


@dataclass(frozen=True)
class TemplateInstantiationResult:
    template_id: str
    release_id: str
    project: Project
    replayed: bool = False


def _template_request_fingerprint(
    *,
    template_id: str,
    release_id: str | None,
    project_name: str | None,
    project_description: str | None,
    org_id: str,
) -> dict[str, object]:
    """Describe only caller-controlled facts, never mutable Registry state."""

    return {
        "kind": _TEMPLATE_REQUEST_KIND,
        "version": 1,
        "template_id": template_id,
        "requested_release_id": release_id,
        "project_name": project_name,
        "project_description": project_description,
        "org_id": org_id,
    }


def _template_result_from_replay(
    replay: ProjectCreationReplay,
    *,
    template_id: str,
    org_id: str,
) -> TemplateInstantiationResult:
    metadata = replay.result_metadata
    if metadata.get("kind") != _TEMPLATE_RESULT_KIND:
        raise RuntimeError("Template replay has invalid durable result metadata")
    if metadata.get("template_id") != template_id:
        raise RuntimeError("Template replay metadata does not match the request")
    release_id = metadata.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise RuntimeError("Template replay is missing its resolved release id")
    if str(replay.project.org_id) != org_id:
        raise RuntimeError("Template replay crossed its persisted organization boundary")
    return TemplateInstantiationResult(
        template_id=template_id,
        release_id=release_id,
        project=replay.project,
        replayed=True,
    )


class TemplateInstantiationService:
    def __init__(
        self,
        *,
        registry: TemplateRegistryService,
        control_plane: ProjectControlPlaneService,
        entitlements: EntitlementService,
        version_engine: VersionWriteEngine,
        write_commands: VersionWriteCommandService,
    ) -> None:
        self.registry = registry
        self.control_plane = control_plane
        self.entitlements = entitlements
        self.version_engine = version_engine
        self.write_commands = write_commands

    async def instantiate(
        self,
        *,
        template_id: str,
        release_id: str | None,
        project_name: str | None,
        project_description: str | None,
        org_id: str,
        actor_user_id: str,
        operation_key: str,
    ) -> TemplateInstantiationResult:
        request_fingerprint = _template_request_fingerprint(
            template_id=template_id,
            release_id=release_id,
            project_name=project_name,
            project_description=project_description,
            org_id=org_id,
        )
        replay = await asyncio.to_thread(
            self.control_plane.preflight_project_creation,
            operation_key=operation_key,
            actor_user_id=actor_user_id,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return _template_result_from_replay(
                replay,
                template_id=template_id,
                org_id=org_id,
            )

        status = self.registry.status()
        if not status.instantiation_enabled:
            from .exceptions import TemplateRegistryUnavailableError

            raise TemplateRegistryUnavailableError(
                status.reason or "template instantiation is disabled"
            )

        # The complete artifact is downloaded and verified before creating any
        # destination state. This is the most important failure boundary.
        resolved = await self.registry.resolve_release(
            template_id=template_id,
            release_id=release_id,
        )
        project_limit = await asyncio.to_thread(
            self.entitlements.enforced_limit_value,
            org_id,
            "projects.max",
        )

        async def initialize(project: Project) -> None:
            await self.write_commands.bulk_write(
                str(project.id),
                resolved.bundle.files,
                actor=actor_user_id,
                message=f"template:{template_id}@{resolved.release.id}",
            )

        publication = await create_project_with_tree(
            control_plane=self.control_plane,
            version_engine=self.version_engine,
            operation_key=operation_key,
            name=project_name or resolved.template.name,
            description=(
                project_description
                if project_description is not None
                else resolved.template.description
            ),
            org_id=org_id,
            created_by=actor_user_id,
            project_limit=project_limit,
            publication_mode="deferred",
            source_fingerprint={
                "kind": "template-instantiation",
                "template_id": template_id,
                "release_id": resolved.release.id,
                "bundle_sha256": resolved.release.bundle_sha256,
            },
            request_fingerprint=request_fingerprint,
            result_metadata={
                "kind": _TEMPLATE_RESULT_KIND,
                "template_id": template_id,
                "release_id": resolved.release.id,
                "bundle_sha256": resolved.release.bundle_sha256,
            },
            initialize=initialize,
        )
        if publication.replayed:
            # A concurrent request may have won after this request's not-found
            # preflight. Never shape the response from the later Registry view;
            # re-read the winner's durable release metadata instead.
            replay = await asyncio.to_thread(
                self.control_plane.preflight_project_creation,
                operation_key=operation_key,
                actor_user_id=actor_user_id,
                request_fingerprint=request_fingerprint,
            )
            if replay is None:
                raise RuntimeError("Replayed template publication has no durable operation")
            return _template_result_from_replay(
                replay,
                template_id=template_id,
                org_id=org_id,
            )
        return TemplateInstantiationResult(
            template_id=template_id,
            release_id=resolved.release.id,
            project=publication.project,
            replayed=publication.replayed,
        )
