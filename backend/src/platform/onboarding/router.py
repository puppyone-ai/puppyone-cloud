"""First sign-in HTTP boundary. Clients keep calling it under /auth."""

from fastapi import APIRouter, Depends

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import CurrentUser, get_current_user
from src.platform.onboarding.dependencies import get_onboarding_service
from src.platform.onboarding.schemas import InitializeResponse
from src.platform.onboarding.service import OnboardingService

router = APIRouter(prefix="/auth", tags=["auth"])


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
