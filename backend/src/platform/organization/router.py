import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request, status

from src.common_schemas import ApiResponse
from src.config import settings
from src.exceptions import ForbiddenException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.entitlements.service import EntitlementService
from src.platform.organization.dependencies import get_org_service
from src.platform.organization.models import OrgInvitation
from src.platform.organization.schemas import (
    CreateOrganization,
    InviteMember,
    OrganizationAccessOut,
    OrganizationOut,
    OrganizationSeatUsageOut,
    OrgInvitationOut,
    OrgMemberOut,
    TransferOwnership,
    UpdateMemberRole,
    UpdateOrganization,
)
from src.platform.organization.service import InviteEmailResult, OrganizationService


def _resolve_frontend_origin(request: Request) -> str:
    """Return the public origin where ``/invite/{token}`` lives.

    Priority order:
      1. ``FRONTEND_URL`` env var — explicit, set per deployment.
      2. ``Origin`` request header — covers same-origin browser calls.
      3. ``Referer`` request header (scheme://host) — last-ditch when
         the browser sent Referer but stripped Origin (rare, but
         happens behind certain CORS reverse proxies).

    Falls back to a sentinel ``http://localhost:3000`` so dev still
    produces a clickable URL when nothing is configured; ops will
    notice immediately and set ``FRONTEND_URL`` in env.
    """
    explicit = (os.environ.get("FRONTEND_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
    return "http://localhost:3000"


def _invitation_to_out(
    inv: OrgInvitation,
    *,
    invite_url: str | None = None,
    include_token: bool = False,
    email_result: InviteEmailResult | None = None,
) -> OrgInvitationOut:
    """Common projection from the domain model to the API schema.

    ``include_token`` gates the (semi-secret) token field so we only
    emit it on the owner-only routes that need it; the (already
    owner-only) listing endpoint also returns it because admins use it
    to copy/share links after the original "send" response is gone.
    """
    return OrgInvitationOut(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        status=inv.status,
        expires_at=inv.expires_at.isoformat(),
        created_at=inv.created_at.isoformat(),
        token=inv.token if include_token else None,
        invite_url=invite_url,
        email_sent=email_result.sent if email_result else False,
        email_error=email_result.error if email_result else None,
    )


router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
)


def _org_to_out(org) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        avatar_url=org.avatar_url,
        plan=org.plan,
        seat_limit=org.seat_limit,
        created_at=org.created_at.isoformat(),
    )


@router.get("/", response_model=ApiResponse[list[OrganizationOut]])
def list_organizations(
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    orgs = org_service.list_my_orgs(current_user.user_id)
    return ApiResponse.success(data=[_org_to_out(org) for org in orgs])


@router.post("/", response_model=ApiResponse[OrganizationOut], status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: CreateOrganization,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org = await asyncio.to_thread(
        org_service.create,
        name=payload.name,
        slug=payload.slug,
        user_id=current_user.user_id,
    )
    if settings.ENTITLEMENTS_MODE == "db":
        from src.platform.billing.provisioning import get_entitlement_provisioning_service

        await get_entitlement_provisioning_service().ensure(
            org_id=org.id,
            actor_user_id=current_user.user_id,
        )
    return ApiResponse.success(data=_org_to_out(org))


@router.get("/{org_id}", response_model=ApiResponse[OrganizationOut])
def get_organization(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not org_service.get_my_role(org_id, current_user.user_id):
        raise ForbiddenException("Not a member of this organization")
    org = org_service.get_by_id(org_id)
    return ApiResponse.success(data=_org_to_out(org))


@router.get("/{org_id}/entitlements", response_model=ApiResponse[dict])
def get_organization_entitlements(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    entitlement_service: EntitlementService = Depends(get_entitlement_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not org_service.get_my_role(org_id, current_user.user_id):
        raise ForbiddenException("Not a member of this organization")
    snapshot = entitlement_service.get_snapshot(org_id)
    return ApiResponse.success(data=snapshot.model_dump(mode="json"))


@router.get("/{org_id}/seat-usage", response_model=ApiResponse[OrganizationSeatUsageOut])
def get_organization_seat_usage(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    quantity = org_service.get_billable_seat_quantity(org_id, current_user.user_id)
    return ApiResponse.success(data=OrganizationSeatUsageOut(billable_seat_quantity=quantity))


@router.get("/{org_id}/access", response_model=ApiResponse[OrganizationAccessOut])
def get_organization_access(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    role = org_service.get_my_role(org_id, current_user.user_id)
    if role is None:
        raise ForbiddenException("Not a member of this organization")
    return ApiResponse.success(
        data=OrganizationAccessOut(
            org_id=org_id,
            user_id=current_user.user_id,
            role=role,
            can_manage_billing=role == "owner",
        )
    )


@router.put("/{org_id}", response_model=ApiResponse[OrganizationOut])
def update_organization(
    org_id: str,
    payload: UpdateOrganization,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    update_data = payload.model_dump(exclude_none=True)
    org = org_service.update(org_id, current_user.user_id, **update_data)
    return ApiResponse.success(data=_org_to_out(org))


@router.delete("/{org_id}", response_model=ApiResponse[None])
async def delete_organization(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await asyncio.to_thread(org_service.delete, org_id, current_user.user_id)
    return ApiResponse.success(message="Organization deleted")


# ── Members ──


@router.get("/{org_id}/members", response_model=ApiResponse[list[OrgMemberOut]])
def list_members(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    rows = org_service.list_members(org_id, current_user.user_id)
    result = []
    for row in rows:
        profile = row.get("profiles") or {}
        result.append(
            OrgMemberOut(
                id=row["id"],
                user_id=row["user_id"],
                email=profile.get("email"),
                display_name=profile.get("display_name"),
                avatar_url=profile.get("avatar_url"),
                role=row["role"],
                joined_at=row["joined_at"],
            )
        )
    return ApiResponse.success(data=result)


@router.put("/{org_id}/members/{target_user_id}/role", response_model=ApiResponse[None])
def update_member_role(
    org_id: str,
    target_user_id: str,
    payload: UpdateMemberRole,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org_service.update_member_role(org_id, target_user_id, payload.role, current_user.user_id)
    return ApiResponse.success(message="Role updated")


@router.post("/{org_id}/ownership/transfer", response_model=ApiResponse[None])
def transfer_ownership(
    org_id: str,
    payload: TransferOwnership,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org_service.transfer_ownership(org_id, payload.user_id, current_user.user_id)
    return ApiResponse.success(message="Ownership transferred")


@router.delete("/{org_id}/members/{target_user_id}", response_model=ApiResponse[None])
def remove_member(
    org_id: str,
    target_user_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org_service.remove_member(org_id, target_user_id, current_user.user_id)
    return ApiResponse.success(message="Member removed")


@router.post("/{org_id}/leave", response_model=ApiResponse[None])
def leave_organization(
    org_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    org_service.leave(org_id, current_user.user_id)
    return ApiResponse.success(message="Left organization")


# ── Invitations ──


@router.post("/{org_id}/invite", response_model=ApiResponse[OrgInvitationOut])
def invite_member(
    org_id: str,
    payload: InviteMember,
    request: Request,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    invitation = org_service.invite(org_id, payload.email, payload.role, current_user.user_id)
    invite_url = f"{_resolve_frontend_origin(request)}/invite/{invitation.token}"

    # Best-effort email dispatch. Failures don't roll back the
    # invitation — the inviter still gets the URL back to share
    # manually. The response carries ``email_sent`` so the UI can
    # adjust messaging accordingly.
    org = org_service.get_by_id(org_id)
    email_result = org_service.send_invite_email(invitation, invite_url, org.name)

    return ApiResponse.success(
        data=_invitation_to_out(
            invitation,
            invite_url=invite_url,
            include_token=True,
            email_result=email_result,
        )
    )


@router.get("/{org_id}/invitations", response_model=ApiResponse[list[OrgInvitationOut]])
def list_invitations(
    org_id: str,
    request: Request,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    invitations = org_service.list_invitations(org_id, current_user.user_id)
    origin = _resolve_frontend_origin(request)
    result = [
        _invitation_to_out(
            inv,
            invite_url=f"{origin}/invite/{inv.token}",
            include_token=True,
        )
        for inv in invitations
    ]
    return ApiResponse.success(data=result)


@router.delete("/{org_id}/invitations/{invitation_id}", response_model=ApiResponse[None])
def revoke_invitation(
    org_id: str,
    invitation_id: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Cancel a pending invitation so its link stops working.

    Owner-only. Idempotent: revoking a non-existent / already-revoked
    invitation returns 200 with the same shape (the caller's goal is
    "this invite is gone").
    """
    org_service.revoke_invitation(org_id, invitation_id, current_user.user_id)
    return ApiResponse.success(message="Invitation revoked")


@router.post("/invitations/{token}/accept", response_model=ApiResponse[dict])
def accept_invitation(
    token: str,
    org_service: OrganizationService = Depends(get_org_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Accept the invitation and return the joined org details.

    Returning the org name + slug lets the frontend show
    "Joined {name}" and bounce the user into their new workspace
    without a second round-trip. Idempotent if the user is already
    a member of the org (the service returns the existing membership).
    """
    member, org = org_service.accept_invitation(
        token, current_user.user_id, user_email=current_user.email
    )
    return ApiResponse.success(
        data={
            "member_id": member.id,
            "org_id": org.id,
            "org_name": org.name,
            "org_slug": org.slug,
            "role": member.role,
        },
        message="Invitation accepted",
    )
