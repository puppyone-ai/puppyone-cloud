"""
User Initialization Service

Idempotently ensures new users have a complete profile + organization +
membership.

The Postgres trigger only creates the profile row; everything else is
handled here at the application layer so the trigger stays minimal and
the logic stays in testable Python.

`ensure_initialized` is **sync**, so it is safe to call from sync FastAPI
route dependencies (it is the safety net behind `resolve_org_id`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.utils.logger import log_error, log_info

if TYPE_CHECKING:
    from src.platform.organization.repository import OrganizationRepository
    from src.platform.profile.repository import ProfileRepositorySupabase


class UserInitializationService:
    """Idempotent user initialization: same result no matter how many times it runs."""

    def __init__(
        self,
        profile_repo: ProfileRepositorySupabase,
        org_repo: OrganizationRepository,
    ):
        self._profile_repo = profile_repo
        self._org_repo = org_repo

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
            org, is_new_org = self._create_default_org(user_id, name)
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

    def _create_default_org(self, user_id: str, name: str):
        org, created = self._org_repo.ensure_personal(user_id, name)

        from src.config import settings

        if created and settings.ENTITLEMENTS_MODE == "db":
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

        return org, created
