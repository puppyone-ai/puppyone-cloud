"""
Project Service Layer

Handles business logic for Project
"""

import logging
from dataclasses import dataclass

from src.exceptions import ErrorCode, NotFoundException, PermissionException
from src.platform.authorization.repository import ProjectMembershipRepository
from src.platform.project.models import Project
from src.platform.project.repository import ProjectRepositoryBase

logger = logging.getLogger(__name__)

@dataclass
class TableInfo:
    """Table information"""

    id: str
    name: str
    rows: int | None = None


class ProjectService:
    """Encapsulates business logic for projects"""

    def __init__(
        self,
        repo: ProjectRepositoryBase,
        memberships: ProjectMembershipRepository,
        seat_billing_service=None,
    ):
        self.repo = repo
        self.memberships = memberships
        self._seat_billing_service = seat_billing_service

    @property
    def _seat_billing(self):
        if self._seat_billing_service is None:
            from src.platform.billing.seats import SeatBillingService
            from src.platform.organization.repository import OrganizationRepository

            self._seat_billing_service = SeatBillingService(OrganizationRepository())
        return self._seat_billing_service

    def _project_org_id(self, project_id: str) -> str:
        project = self.get_by_id(project_id)
        if project is None:
            raise NotFoundException(f"Project not found: {project_id}", code=ErrorCode.NOT_FOUND)
        return project.org_id

    def get_by_id(self, project_id: str) -> Project | None:
        """
        Get project by ID

        Args:
            project_id: Project ID (UUID)

        Returns:
            Project object, or None if not found
        """
        return self.repo.get_by_id(project_id)

    def get_by_org_id(self, org_id: str) -> list[Project]:
        """
        Get all projects under an organization

        Args:
            org_id: Organization ID

        Returns:
            List of projects
        """
        return self.repo.get_by_org_id(org_id)

    def update(
        self,
        project_id: str,
        name: str | None,
        description: str | None,
        visibility: str | None = None,
        bound_git_branch: str | None = None,
    ) -> Project:
        """
        Update a project

        Args:
            project_id: Project ID
            name: Project name (optional)
            description: Project description (optional)
            bound_git_branch: Default git branch (optional)

        Returns:
            Updated Project object

        Raises:
            NotFoundException: If project does not exist
        """
        updated = self.repo.update(
            project_id=project_id,
            name=name,
            description=description,
            visibility=visibility,
            bound_git_branch=bound_git_branch,
        )
        if not updated:
            raise NotFoundException(f"Project not found: {project_id}", code=ErrorCode.NOT_FOUND)
        return updated

    def list_project_members(self, project_id: str) -> list:
        rows = self.memberships.list_by_project(project_id)

        # ── Backfill missing profile data from auth.users ─────────────
        # The PostgREST join returns ``profiles=None`` (or fields all
        # null) when the user has an auth.users row but no profiles
        # row — common for users who signed up before the profile-on-
        # signup hook existed, or whose profile got hidden by RLS.
        # Without this, the frontend falls back to "Unknown member"
        # which is alarming for what's actually a normal user. Pull
        # email straight from ``auth.users`` via the admin SDK so the
        # name resolution chain has something to land on.
        missing_user_ids = []
        for row in rows:
            profile = row.get("profiles")
            if not profile or not profile.get("email"):
                uid = row.get("user_id")
                if uid:
                    missing_user_ids.append(uid)

        if missing_user_ids:
            email_by_user_id = self._lookup_auth_users_email(missing_user_ids)
            for row in rows:
                if email_by_user_id.get(row.get("user_id")):
                    profile = row.get("profiles") or {}
                    if not profile.get("email"):
                        profile["email"] = email_by_user_id[row["user_id"]]
                        row["profiles"] = profile

        return rows

    def _lookup_auth_users_email(self, user_ids: list[str]) -> dict[str, str]:
        """Look up ``{user_id: email}`` from ``auth.users`` for users
        whose ``public.profiles`` row is missing.

        Uses the Supabase service-role client (already injected via
        ``SupabaseClient``). Returns an empty dict on any failure —
        the caller treats missing emails as "fall back to user id
        prefix on the frontend", which is the same path it'd hit
        without this lookup, so we never want to fail loudly here.
        """
        from src.infra.supabase.client import SupabaseClient

        try:
            client = SupabaseClient().client
        except Exception:
            return {}
        out: dict[str, str] = {}
        for uid in user_ids:
            try:
                resp = client.auth.admin.get_user_by_id(uid)
                user = getattr(resp, "user", None) or resp
                email = getattr(user, "email", None)
                if email:
                    out[uid] = email
            except Exception:
                # Continue — one missing user shouldn't break the
                # whole list.
                continue
        return out

    def add_project_member(
        self,
        project_id: str,
        target_user_id: str,
        role: str,
        *,
        granted_by: str | None = None,
    ) -> dict:
        if granted_by is None:
            raise PermissionException(
                "Project member changes require an authenticated actor",
                code=ErrorCode.FORBIDDEN,
            )
        from src.config import settings
        from src.platform.billing.seats import is_billable_project_role

        org_id = self._project_org_id(project_id)
        was_billable = False
        activation_operation = None
        if settings.SEAT_BILLING_MODE != "disabled":
            was_billable = self.memberships.is_billable_organization_member(org_id, target_user_id)
        if (
            settings.SEAT_BILLING_MODE != "disabled"
            and is_billable_project_role(role)
            and not was_billable
        ):
            activation_operation = self._seat_billing.ensure_member_activation(
                org_id=org_id,
                subject_user_id=target_user_id,
                role=f"project:{role}",
                actor_user_id=granted_by,
                grants_billable_capability=True,
            )
        row = self.memberships.add(project_id, target_user_id, role, granted_by)
        if not row:
            raise PermissionException("Project member could not be added", code=ErrorCode.FORBIDDEN)
        if activation_operation is not None:
            self._seat_billing.complete_member_activation(activation_operation)
        return row

    def update_project_member_role(
        self,
        project_id: str,
        target_user_id: str,
        role: str,
        *,
        actor_user_id: str,
    ) -> dict:
        from src.config import settings
        from src.platform.billing.seats import is_billable_project_role

        org_id = self._project_org_id(project_id)
        existing = None
        was_billable = False
        activation_operation = None
        if settings.SEAT_BILLING_MODE != "disabled":
            existing = self.memberships.get(project_id, target_user_id)
            if existing is None:
                raise NotFoundException("Project member not found", code=ErrorCode.NOT_FOUND)
            was_billable = self.memberships.is_billable_organization_member(org_id, target_user_id)
        if (
            settings.SEAT_BILLING_MODE != "disabled"
            and is_billable_project_role(role)
            and not was_billable
        ):
            activation_operation = self._seat_billing.ensure_member_activation(
                org_id=org_id,
                subject_user_id=target_user_id,
                role=f"project:{role}",
                actor_user_id=actor_user_id,
                grants_billable_capability=True,
            )
        row = self.memberships.update_role(project_id, target_user_id, role, actor_user_id)
        if not row:
            raise NotFoundException("Project member not found", code=ErrorCode.NOT_FOUND)
        if activation_operation is not None:
            self._seat_billing.complete_member_activation(activation_operation)
        if settings.SEAT_BILLING_MODE != "disabled" and was_billable:
            remains_billable = self.memberships.is_billable_organization_member(
                org_id, target_user_id
            )
            if not remains_billable:
                self._seat_billing.record_member_deactivation(
                    org_id=org_id,
                    subject_user_id=target_user_id,
                    actor_user_id=actor_user_id,
                    previous_role=f"project:{existing['role']}",
                    was_billable=True,
                )
        return row

    def remove_project_member(
        self, project_id: str, target_user_id: str, *, actor_user_id: str
    ) -> None:
        from src.config import settings

        org_id = self._project_org_id(project_id)
        existing = None
        was_billable = False
        if settings.SEAT_BILLING_MODE != "disabled":
            existing = self.memberships.get(project_id, target_user_id)
            was_billable = self.memberships.is_billable_organization_member(org_id, target_user_id)
        changed = self.memberships.remove(project_id, target_user_id, actor_user_id)
        if not changed:
            raise NotFoundException("Project member not found", code=ErrorCode.NOT_FOUND)
        if was_billable and not self.memberships.is_billable_organization_member(
            org_id, target_user_id
        ):
            self._seat_billing.record_member_deactivation(
                org_id=org_id,
                subject_user_id=target_user_id,
                actor_user_id=actor_user_id,
                previous_role=f"project:{(existing or {}).get('role', 'unknown')}",
                was_billable=True,
            )

    # ── Share link MVP ──

    def get_share_info(self, project_id: str, user_id: str) -> dict:
        """Return the current share token for owners/admins.

        Authorization: org owner OR project admin only — sharing a
        link is a sensitive action that effectively widens the
        membership ACL, so we gate it tighter than read access.

        Returns ``{share_token: str, can_share: bool}``. ``can_share``
        is always True when this method succeeds (the auth gate raises
        otherwise); we keep the field so the frontend can branch on a
        single property when there are future role tiers.
        """
        project = self.get_by_id(project_id)
        if not project:
            raise NotFoundException(
                f"Project not found: {project_id}",
                code=ErrorCode.NOT_FOUND,
            )
        return {
            "share_token": project.share_token or "",
            "can_share": True,
        }

    def rotate_share_token(self, project_id: str, user_id: str) -> dict:
        """Generate a new share token; old token stops working.

        Owner/admin only — see ``get_share_info``.
        """
        updated = self.repo.rotate_share_token(project_id)
        if not updated:
            raise NotFoundException(
                f"Project not found: {project_id}",
                code=ErrorCode.NOT_FOUND,
            )
        return {
            "share_token": updated.share_token or "",
            "can_share": True,
        }

    def join_via_share_token(self, token: str, user_id: str) -> dict:
        """Join a project by presenting a valid share token.

        Default role is ``viewer`` — share links are low-trust by
        design. Idempotent: if the user is already a project member
        we silently return their existing role rather than 409 (the
        observable goal — "I'm in this project now" — is met).
        """
        row = self.memberships.join_with_share_token(token, user_id)
        if not row:
            raise NotFoundException(
                "Share link is invalid or has been rotated",
                code=ErrorCode.NOT_FOUND,
            )
        return row
