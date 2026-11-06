"""First sign-in HTTP boundary. Clients keep calling it under /auth."""

from fastapi import APIRouter, Depends

from src.common_schemas import ApiResponse
from src.config import settings
from src.exceptions import ForbiddenException
from src.infra.execution.runner import run_blocking
from src.platform.auth.dependencies import CurrentUser, get_current_user, get_initialization_service
from src.platform.auth.initialization import UserInitializationService
from src.platform.billing.provisioning import get_entitlement_provisioning_service
from src.platform.onboarding.dependencies import get_onboarding_service
from src.platform.onboarding.schemas import InitializeResponse
from src.platform.onboarding.service import OnboardingService
from src.platform.organization.dependencies import get_org_service
from src.platform.organization.schemas import OrganizationOut
from src.platform.organization.service import OrganizationService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/personal-space", response_model=ApiResponse[OrganizationOut])
async def ensure_personal_space(
    current_user: CurrentUser = Depends(get_current_user),
    initialization: UserInitializationService = Depends(get_initialization_service),
    organizations: OrganizationService = Depends(get_org_service),
):
    """Ensure the signed-in person's destination, never migrate existing content."""
    if current_user.is_anonymous or current_user.role != "authenticated":
        raise ForbiddenException("A signed-in person is required")
    name = str((current_user.user_metadata or {}).get("full_name")
               or (current_user.email or "").split("@")[0] or "My Cloud")
    await run_blocking(initialization.ensure_initialized, user_id=current_user.user_id,
                       email=current_user.email or "", display_name=name)
    org = await run_blocking(organizations.ensure_personal, current_user.user_id, name)
    if settings.ENTITLEMENTS_MODE == "db":
        await get_entitlement_provisioning_service().ensure(org_id=org.id, actor_user_id=current_user.user_id)
    return ApiResponse.success(data=OrganizationOut.for_member(org))


@router.post("/initialize", response_model=ApiResponse[InitializeResponse])
async def initialize_user(
    current_user: CurrentUser = Depends(get_current_user),
    onboarding: OnboardingService = Depends(get_onboarding_service),
):
    """Idempotent user initialization: ensures profile + default org +
    membership exist, and on first sign-in seeds a "Get Started" demo
    project so the post-login redirect can land the user inside it
    instead of an empty dashboard."""
    return ApiResponse.success(
        data=await onboarding.initialize_user(
            user_id=current_user.user_id,
            email=current_user.email,
            display_name=current_user.user_metadata.get("full_name")
            if current_user.user_metadata
            else None,
        )
    )
