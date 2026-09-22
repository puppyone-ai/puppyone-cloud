"""Canonical application API for template discovery and instantiation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService
from src.platform.idempotency import mark_idempotency_replay, require_idempotency_key
from src.platform.organization.dependencies import resolve_org_id
from src.platform.project.presenters import project_to_out

from .dependencies import (
    get_template_instantiation_service,
    get_template_registry_service,
)
from .exceptions import TemplateRegistryError
from .http_errors import registry_http_exception
from .instantiation import TemplateInstantiationService
from .schemas import (
    TEMPLATE_ID_PATTERN,
    TemplateCatalog,
    TemplateDetail,
    TemplateInstantiation,
    TemplateInstantiationRequest,
    TemplateRegistryStatus,
)
from .service import TemplateRegistryService

router = APIRouter(prefix="/templates", tags=["templates"])
TemplateId = Annotated[str, Path(pattern=TEMPLATE_ID_PATTERN)]


@router.get("/status", response_model=ApiResponse[TemplateRegistryStatus])
def get_registry_status(
    registry: TemplateRegistryService = Depends(get_template_registry_service),
):
    return ApiResponse.success(data=registry.status(), message="Template Registry status")


@router.get("", response_model=ApiResponse[TemplateCatalog])
async def list_templates(
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=100),
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    registry: TemplateRegistryService = Depends(get_template_registry_service),
):
    try:
        catalog = await registry.catalog(
            query=q,
            category=category,
            cursor=cursor,
            limit=limit,
        )
    except TemplateRegistryError as exc:
        raise registry_http_exception(exc) from exc
    return ApiResponse.success(data=catalog, message="Templates retrieved")


@router.get("/{template_id}", response_model=ApiResponse[TemplateDetail])
async def get_template(
    template_id: TemplateId,
    registry: TemplateRegistryService = Depends(get_template_registry_service),
):
    try:
        detail = await registry.get_template(template_id)
    except TemplateRegistryError as exc:
        raise registry_http_exception(exc) from exc
    return ApiResponse.success(data=detail, message="Template retrieved")


@router.post(
    "/{template_id}/instantiate",
    response_model=ApiResponse[TemplateInstantiation],
    status_code=status.HTTP_201_CREATED,
)
async def instantiate_template(
    template_id: TemplateId,
    payload: TemplateInstantiationRequest,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    instantiation: TemplateInstantiationService = Depends(get_template_instantiation_service),
    authorization: AuthorizationService = Depends(get_authorization_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org_id = resolve_org_id(payload.org_id, current_user.user_id)
    try:
        result = await instantiation.instantiate(
            template_id=template_id,
            release_id=payload.release_id,
            project_name=payload.name,
            project_description=payload.description,
            org_id=org_id,
            actor_user_id=current_user.user_id,
            operation_key=idempotency_key,
        )
    except TemplateRegistryError as exc:
        raise registry_http_exception(exc) from exc

    grant = authorization.authorize(
        str(result.project.id),
        current_user.user_id,
        ProjectAction.PROJECT_READ,
    )
    response.status_code = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
    mark_idempotency_replay(response, replayed=result.replayed)
    return ApiResponse.success(
        data=TemplateInstantiation(
            template_id=result.template_id,
            release_id=result.release_id,
            project=project_to_out(result.project, grant),
        ),
        message="Template instantiated",
    )
