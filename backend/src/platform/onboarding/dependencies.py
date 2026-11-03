"""Build the first sign-in use case from the services it drives."""

from src.platform.auth.dependencies import get_initialization_service
from src.platform.onboarding.service import OnboardingService
from src.platform.profile.dependencies import get_profile_repository
from src.platform.project.control_plane_dependencies import get_project_control_plane_service
from src.platform.project.dependencies import build_project_service

_service: OnboardingService | None = None


def get_onboarding_service() -> OnboardingService:
    global _service
    if _service is None:
        _service = OnboardingService(
            initialization=get_initialization_service(),
            profile_repo=get_profile_repository(),
            project_service=build_project_service(),
            project_control_plane=get_project_control_plane_service(),
        )
    return _service
