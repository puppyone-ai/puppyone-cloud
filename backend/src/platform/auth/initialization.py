"""
User Initialization Service

Idempotently ensures new users have a complete profile + organization +
membership, and on the very first sign-in also seeds a "Get Started"
demo project so the user lands on something useful instead of an empty
dashboard.

The Postgres trigger only creates the profile row; everything else is
handled here at the application layer so the trigger stays minimal and
the logic stays in testable Python.

API split (intentional):
* `ensure_initialized` is **sync** — profile + org + membership only.
  Safe to call from sync FastAPI route dependencies (it is the safety
  net behind `resolve_org_id`).
* `maybe_seed_demo_project` is **async** because writing the seed
  template goes through ProductOperationAdapter. Auth routes call this *after* the sync
  step to grab a demo project id for the post-login redirect.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING
from uuid import UUID

from src.utils.logger import log_error, log_info

if TYPE_CHECKING:
    from src.platform.organization.repository import OrganizationRepository
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


class UserInitializationService:
    """Idempotent user initialization: same result no matter how many times it runs."""

    def __init__(
        self,
        profile_repo: ProfileRepositorySupabase,
        org_repo: OrganizationRepository,
        project_service: ProjectService,
        project_control_plane: ProjectControlPlaneService,
    ):
        self._profile_repo = profile_repo
        self._org_repo = org_repo
        self._project_service = project_service
        self._project_control_plane = project_control_plane

    # ── Sync core: profile + org + membership ────────────────────────

    def ensure_initialized(
        self,
        user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> dict:
        """
        Ensure user has profile + default org + membership. Sync because
        every callsite (auth routes, org safety nets, profile service)
        needs this to complete before further work — no need for async
        hop-around on a few Supabase calls.

        Returns:
            {"org_id": str, "is_new_org": bool}
        """
        name = display_name or (email.split("@")[0] if email else "User")

        # 1. Ensure profile exists (trigger usually creates it; this is a safety net)
        profile = self._profile_repo.get_or_create(user_id, email)
        if not profile:
            log_error(f"Failed to get/create profile for user {user_id}")
            raise RuntimeError(f"Cannot initialize user {user_id}: profile creation failed")

        # 2. Ensure at least one organization exists
        orgs = self._org_repo.list_by_user(user_id)
        is_new_org = False

        if not orgs:
            org = self._create_default_org(user_id, name)
            is_new_org = True
            log_info(f"Created default org {org.id} for user {user_id}")
        else:
            org = orgs[0]

        # 3. Ensure profile.default_org_id is set
        if not profile.default_org_id:
            from src.platform.profile.models import ProfileUpdate

            self._profile_repo.update(
                user_id,
                ProfileUpdate(default_org_id=org.id),
            )
            log_info(f"Set default_org_id={org.id} for user {user_id}")

        return {"org_id": org.id, "is_new_org": is_new_org}

    # ── Async demo seeding (post-init, sign-in routes only) ──────────

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

    # ── Internals ────────────────────────────────────────────────────

    async def _seed_demo_project(
        self,
        user_id: str,
        org_id: str,
    ) -> str | None:
        """Create the Get Started project, init its version tree, and seed
        template content. Returns the new project id, or None on failure
        (failures are logged but never block sign-in)."""
        from src.platform.project.orchestration import create_project_with_tree
        from src.platform.project.templates import seed_template_content
        from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

        try:
            from src.platform.entitlements.service import EntitlementService

            project_limit = await asyncio.to_thread(
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

    def _create_default_org(self, user_id: str, name: str):
        slug = f"personal-{user_id}"

        org = self._org_repo.create(
            name=f"{name}'s Workspace",
            slug=slug,
            created_by=user_id,
        )

        self._org_repo.add_member(
            org_id=org.id,
            user_id=user_id,
            role="owner",
        )

        from src.config import settings

        if settings.ENTITLEMENTS_MODE == "db":
            try:
                from src.platform.billing.provisioning import EntitlementProvisioningService

                EntitlementProvisioningService().enqueue(
                    org_id=org.id,
                    actor_user_id=user_id,
                )
            except Exception as exc:
                log_error(
                    "Failed to enqueue initial entitlement provisioning "
                    f"org={org.id} error_type={type(exc).__name__}"
                )

        return org
