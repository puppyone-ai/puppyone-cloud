from typing import Literal

from pydantic import BaseModel

from src.platform.organization.models import Organization


class CreateOrganization(BaseModel):
    name: str
    slug: str | None = None


class UpdateOrganization(BaseModel):
    name: str | None = None
    avatar_url: str | None = None


class InviteMember(BaseModel):
    email: str
    role: str = "member"


class UpdateMemberRole(BaseModel):
    role: str


class TransferOwnership(BaseModel):
    user_id: str


class OrganizationOut(BaseModel):
    id: str
    kind: Literal["personal", "team"] = "team"
    can_create_projects: bool = True
    name: str
    slug: str
    avatar_url: str | None = None
    plan: str
    seat_limit: int
    created_at: str
    member_count: int | None = None
    # The plan billing published for this space, so the Homepage can say which
    # plan its projects use. Absent until billing has published one.
    billing_plan_id: str | None = None

    @classmethod
    def for_member(cls, org: Organization) -> "OrganizationOut":
        """Only call after membership admission; Project creation rechecks it in SQL."""
        return cls(
            id=org.id, kind="personal" if org.personal_owner_id is not None else "team",
            can_create_projects=True, name=org.name, slug=org.slug, avatar_url=org.avatar_url,
            plan=org.plan, seat_limit=org.seat_limit, created_at=org.created_at.isoformat(),
        )


class OrganizationSeatUsageOut(BaseModel):
    """Product-derived seat usage; commercial limits remain in PuppyPay."""

    billable_seat_quantity: int


class OrganizationAccessOut(BaseModel):
    """Current-user organization authority without exposing the member directory."""

    org_id: str
    user_id: str
    role: str
    can_manage_billing: bool


class OrgMemberOut(BaseModel):
    id: str
    user_id: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    role: str
    joined_at: str


class OrgInvitationOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    expires_at: str
    created_at: str
    # ``token`` and ``invite_url`` let the inviter copy and share the
    # link manually — required because the server's email delivery is
    # best-effort and won't reach every inbox (existing-user accounts,
    # spam folders, dev environments without SMTP). Owner-only via
    # ``_require_owner`` on the routes that emit this schema.
    token: str | None = None
    invite_url: str | None = None
    # ``email_sent`` reflects whether the best-effort Supabase email
    # dispatch returned ok. The invitation is still valid even when
    # this is False — the inviter just has to share the URL manually.
    email_sent: bool = False
    email_error: str | None = None
