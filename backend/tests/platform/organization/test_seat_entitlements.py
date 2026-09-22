from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.organization.models import Organization, OrgInvitation, OrgMember
from src.platform.organization.service import OrganizationService

NOW = datetime.now(UTC)


class _FakeOrganizationRepository:
    def __init__(self) -> None:
        self.invitation = OrgInvitation(
            id="inv-1",
            org_id="org-1",
            email="new@example.com",
            role="member",
            token="token-1",
            status="pending",
            invited_by="owner-1",
            expires_at=NOW + timedelta(days=1),
            created_at=NOW,
        )

    def get_by_id(self, org_id: str) -> Organization | None:
        if org_id != "org-1":
            return None
        return Organization(
            id="org-1",
            name="Org",
            slug="org",
            created_by="owner-1",
            created_at=NOW,
            updated_at=NOW,
            seat_limit=1,
        )

    def get_member(self, org_id: str, user_id: str) -> OrgMember | None:
        if org_id == "org-1" and user_id == "owner-1":
            return OrgMember(
                id="member-owner",
                org_id="org-1",
                user_id="owner-1",
                role="owner",
                joined_at=NOW,
            )
        return None

    def count_members(self, org_id: str) -> int:
        assert org_id == "org-1"
        return 1

    def count_billable_members(self, org_id: str) -> int:
        assert org_id == "org-1"
        return 1

    def create_invitation(self, *args, **kwargs):
        return self.invitation

    def get_invitation_by_token(self, token: str) -> OrgInvitation | None:
        return self.invitation if token == "token-1" else None

    def add_member(self, *args, **kwargs):
        raise AssertionError("seat limit should be enforced before adding a member")


def test_invite_does_not_consume_or_grant_a_paid_seat() -> None:
    service = OrganizationService(_FakeOrganizationRepository())
    invitation = service.invite("org-1", "new@example.com", "member", "owner-1")
    assert invitation.status == "pending"


def test_billable_seat_usage_is_capability_derived_for_members() -> None:
    service = OrganizationService(_FakeOrganizationRepository())

    assert service.get_billable_seat_quantity("org-1", "owner-1") == 1


def test_billable_seat_usage_rejects_non_members() -> None:
    service = OrganizationService(_FakeOrganizationRepository())

    with pytest.raises(AppException, match="Not a member"):
        service.get_billable_seat_quantity("org-1", "other-user")


def test_accept_invitation_enforces_seat_limit() -> None:
    service = OrganizationService(_FakeOrganizationRepository())

    with pytest.raises(AppException, match="Seat limit reached \\(1\\)"):
        service.accept_invitation(
            "token-1",
            "new-user-1",
            user_email="new@example.com",
        )


class _OwnershipRepository(_FakeOrganizationRepository):
    def __init__(self, target_role: str) -> None:
        super().__init__()
        self.target_role = target_role
        self.transferred = False

    def get_member(self, org_id: str, user_id: str) -> OrgMember | None:
        owner = super().get_member(org_id, user_id)
        if owner:
            return owner
        if org_id == "org-1" and user_id == "target-1":
            return OrgMember(
                id="member-target",
                org_id=org_id,
                user_id=user_id,
                role=self.target_role,
                joined_at=NOW,
            )
        return None

    def transfer_ownership(self, org_id: str, current_owner: str, new_owner: str) -> bool:
        assert (org_id, current_owner, new_owner) == ("org-1", "owner-1", "target-1")
        self.transferred = True
        return True


class _Seats:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def ensure_member_activation(self, **values):
        self.calls += 1
        assert values["role"] == "owner"
        if self.fail:
            raise AppException(code=ErrorCode.FORBIDDEN, status_code=409, message="Seat required")


def test_owner_transfer_is_atomic_for_existing_billable_member() -> None:
    repo = _OwnershipRepository("member")
    seats = _Seats()
    OrganizationService(repo, seat_billing_service=seats).transfer_ownership(
        "org-1",
        "target-1",
        "owner-1",
    )
    assert repo.transferred
    assert seats.calls == 0


def test_owner_transfer_from_viewer_waits_for_paid_seat(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    repo = _OwnershipRepository("viewer")
    seats = _Seats(fail=True)
    with pytest.raises(AppException, match="Seat required"):
        OrganizationService(repo, seat_billing_service=seats).transfer_ownership(
            "org-1",
            "target-1",
            "owner-1",
        )
    assert seats.calls == 1
    assert not repo.transferred
