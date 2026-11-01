"""First sign-in: everything a user needs before landing in the product.

Auth confirms who the user is. This use case spans what comes next: their
account, then their entitlements, then on the very first sign-in a "Get Started"
demo Project, so they land on something useful instead of an empty dashboard.
The order matters, because the demo Project is admitted against those
entitlements.

Each step is idempotent, so a retry or a returning user changes nothing.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from uuid import UUID

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.billing import provisioning
from src.platform.entitlements.service import EntitlementService
from src.platform.onboarding.schemas import InitializeResponse
from src.platform.project.orchestration import create_project_with_tree
from src.platform.project.templates import seed_template_content
from src.utils.logger import log_error, log_info
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

if TYPE_CHECKING:
    from src.platform.auth.initialization import UserInitializationService
    from src.platform.profile.repository import ProfileRepositorySupabase
    from src.platform.project.control_plane import ProjectControlPlaneService
    from src.platform.project.service import ProjectService


# Template used to populate the demo project. Must match an id in
# `src.platform.project.templates.TEMPLATES`.
_DEMO_TEMPLATE_ID = "get-started"
_DEMO_PROJECT_NAME = "Get Started"
_DEMO_PROJECT_DESCRIPTION = (
    "A guided tour of Puppyone. Browse the files, then delete this "
    "project (or rename it) and create your own."
)


def _demo_project_operation_key(user_id: str, org_id: str) -> str:
    """Stable canonical UUIDv4 identity for one user's onboarding Project.

    The value is not a credential.  Setting the UUID version/variant bits on a
    deterministic digest gives retries one accepted UUIDv4 key without adding
    mutable device/session state or creating a fresh Project on every sign-in.
    Bump the namespace suffix only for a genuinely new onboarding intent.
    """

    raw = bytearray(
        hashlib.sha256(f"puppyone:demo-project:v1:{user_id}:{org_id}".encode()).digest()[:16]
    )
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(raw)))


class OnboardingService:
    def __init__(
        self,
        initialization: UserInitializationService,
        profile_repo: ProfileRepositorySupabase,
        project_service: ProjectService,
        project_control_plane: ProjectControlPlaneService,
    ):
        self._initialization = initialization
        self._profile_repo = profile_repo
        self._project_service = project_service
        self._project_control_plane = project_control_plane

    async def initialize_user(
        self,
        user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> InitializeResponse:
        account = self._initialization.ensure_initialized(
            user_id=user_id,
            email=email,
            display_name=display_name,
        )
        org_id = account["org_id"]
        if settings.ENTITLEMENTS_MODE == "db":
            await provisioning.get_entitlement_provisioning_service().ensure(
                org_id=org_id,
                actor_user_id=user_id,
            )
        demo_project_id = await self.maybe_seed_demo_project(user_id=user_id, org_id=org_id)
        return InitializeResponse(
            org_id=org_id,
            is_new_org=account["is_new_org"],
            demo_project_id=demo_project_id,
        )

    async def maybe_seed_demo_project(
        self,
        user_id: str,
        org_id: str,
    ) -> str | None:
        """
        Seed a "Get Started" demo project on the user's very first
        sign-in and return its id so the auth route can land them
        inside it.

        Idempotent: if `has_onboarded` is already true we do nothing
        and return None — returning users go to /home and navigate to
        whichever project they care about. The returned id is *only*
        meant to drive the post-signup redirect, not every login.

        For legacy users (have projects but were never marked as
        onboarded — they signed up before this feature shipped) we
        flip the flag and return None so they also land on /home.

        Failures are logged but never block sign-in — worst case the
        user lands on /home, same as today.
        """
        profile = self._profile_repo.get_by_user_id(user_id)
        if profile is None:
            # Should never happen — ensure_initialized was just called.
            log_error(f"maybe_seed_demo_project: no profile for {user_id}")
            return None

        if profile.has_onboarded:
            return None

        # Legacy user (signed up before this feature shipped) — don't
        # surprise them with a synthetic redirect, just mark them
        # onboarded so we stop checking.
        existing = self._project_service.get_by_org_id(org_id)
        if existing:
            self._profile_repo.mark_onboarded(user_id=user_id)
            log_info(
                f"User {user_id} already had projects; marking onboarded without seeding a demo"
            )
            return None

        # True first-time user → seed the demo.
        demo_id = await self._seed_demo_project(user_id=user_id, org_id=org_id)
        if demo_id:
            self._profile_repo.mark_onboarded(
                user_id=user_id,
                demo_project_id=demo_id,
            )
        return demo_id

    async def _seed_demo_project(
        self,
        user_id: str,
        org_id: str,
    ) -> str | None:
        """Create the Get Started project, init its version tree, and seed
        template content. Returns the new project id, or None on failure
        (failures are logged but never block sign-in)."""
        try:
            project_limit = await run_blocking(
                EntitlementService().enforced_limit_value,
                org_id,
                "projects.max",
            )
            container = build_worker_version_engine_container()
            publication = await create_project_with_tree(
                control_plane=self._project_control_plane,
                version_engine=container.write_engine(),
                operation_key=_demo_project_operation_key(user_id, org_id),
                name=_DEMO_PROJECT_NAME,
                description=_DEMO_PROJECT_DESCRIPTION,
                org_id=org_id,
                created_by=user_id,
                project_limit=project_limit,
                publication_mode="empty",
                source_fingerprint={
                    "kind": "onboarding-demo",
                    "template_id": _DEMO_TEMPLATE_ID,
                    "version": 1,
                },
            )
        except Exception as e:
            log_error(f"Demo project: publication failed for user={user_id} org={org_id}: {e}")
            return None

        project = publication.project
        project_id = str(project.id)

        # The Project is already a valid, ready canonical Git repository.
        # Starter content is an optional post-publication enhancement; a
        # failure can never expose a missing-root/half-created Project.
        try:
            await seed_template_content(
                project_id=project_id,
                template_id=_DEMO_TEMPLATE_ID,
                created_by=user_id,
            )
            log_info(
                f"Demo project {project_id}: seeded with template "
                f"'{_DEMO_TEMPLATE_ID}' for user {user_id}"
            )
        except Exception as e:
            log_error(f"Demo project {project_id}: template seed failed: {e}")

        return project_id
