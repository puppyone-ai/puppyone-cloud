"""Personal destination discovery and human provisioning admission."""
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.exceptions import ForbiddenException
from src.platform.auth.initialization import UserInitializationService
from src.platform.auth.models import CurrentUser
from src.platform.onboarding.router import ensure_personal_space
from src.platform.organization.models import Organization, OrgMember
from src.platform.organization.schemas import OrganizationOut
from src.platform.organization.service import OrganizationService


@pytest.fixture
def personal():
    return Organization(id="space", name="Alex", slug="space", created_by="owner",
                        type="personal", personal_owner_id="owner", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))


def test_legacy_personal_label_is_not_private_identity(personal):
    old = personal.model_copy(update={"personal_owner_id": None})
    assert OrganizationOut.for_member(old).kind == "team"
    out = OrganizationOut.for_member(personal)
    assert out.kind == "personal" and out.can_create_projects
    assert "personal_owner_id" not in out.model_dump()


def test_service_provisions_entitlements_only_for_new_space(personal):
    repo = Mock()
    repo.ensure_personal.return_value = (personal, False)
    service = OrganizationService(repo)
    service._enqueue_entitlement_provisioning = Mock()
    assert service.ensure_personal("owner", "Alex") == personal
    service._enqueue_entitlement_provisioning.assert_not_called()
    repo.ensure_personal.return_value = (personal, True)
    service.ensure_personal("owner", "Alex")
    service._enqueue_entitlement_provisioning.assert_called_once_with("space", "owner")


@pytest.mark.parametrize("operation", ["invite", "transfer_ownership", "update_member_role", "remove_member"])
def test_personal_team_management_is_rejected_before_mutation(personal, operation):
    repo = Mock()
    repo.get_by_id.return_value = personal
    repo.get_member.return_value = OrgMember(id="membership", org_id="space", user_id="owner", role="owner", joined_at=datetime.now(UTC))
    service = OrganizationService(repo)
    arguments = {
        "invite": ("space", "other@example.test", "member", "owner"),
        "transfer_ownership": ("space", "other", "owner"),
        "delete": ("space", "owner"),
        "update_member_role": ("space", "other", "viewer", "owner"),
        "remove_member": ("space", "other", "owner"),
    }
    with pytest.raises(ForbiddenException, match="Personal Cloud"):
        getattr(service, operation)(*arguments[operation])
    repo.create_invitation.assert_not_called()
    repo.transfer_ownership.assert_not_called()
    repo.delete_empty_control_plane.assert_not_called()
    repo.update_member_role.assert_not_called()
    repo.remove_member.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,anonymous", [("service_role", False), ("authenticated", True)])
async def test_nonhuman_cannot_ensure(role, anonymous):
    init, orgs = Mock(), Mock()
    with pytest.raises(ForbiddenException):
        await ensure_personal_space(CurrentUser(user_id="owner", email="owner@example.test", role=role, is_anonymous=anonymous), init, orgs)
    init.ensure_initialized.assert_not_called()
    orgs.ensure_personal.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_uses_current_identity_not_payload(personal, monkeypatch):
    from src.platform.onboarding import router
    monkeypatch.setattr(router.settings, "ENTITLEMENTS_MODE", "static")
    init, orgs = Mock(), Mock()
    orgs.ensure_personal.return_value = personal
    response = await ensure_personal_space(CurrentUser(user_id="owner", email="alex@example.test", role="authenticated"), init, orgs)
    assert response.data.id == "space" and response.data.kind == "personal"
    orgs.ensure_personal.assert_called_once_with("owner", "alex")


def test_personal_deletion_maps_atomic_admission():
    repo = Mock()
    repo.delete_empty_control_plane.return_value = {"outcome": "personal_organization"}
    with pytest.raises(ForbiddenException, match="Personal Cloud"):
        OrganizationService(repo).delete("space", "owner")
    repo.get_member.assert_not_called()


def test_existing_defaults_and_organizations_are_preserved(personal):
    profiles, orgs = Mock(), Mock()
    profiles.get_or_create.return_value = Mock(default_org_id="existing")
    orgs.list_by_user.return_value = [personal.model_copy(update={"id": "existing", "personal_owner_id": None})]
    service = UserInitializationService(profiles, orgs)
    assert service.ensure_initialized("owner", "owner@example.test") == {"org_id": "existing", "is_new_org": False}
    orgs.ensure_personal.assert_not_called()
    profiles.update.assert_not_called()


def test_new_account_uses_atomic_personal_creation(personal, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "static")
    profiles, orgs = Mock(), Mock()
    profiles.get_or_create.return_value = Mock(default_org_id=None)
    orgs.list_by_user.return_value = []
    orgs.ensure_personal.return_value = (personal, True)
    result = UserInitializationService(profiles, orgs).ensure_initialized("owner", "owner@example.test")
    assert result == {"org_id": "space", "is_new_org": True}
    orgs.ensure_personal.assert_called_once_with("owner", "owner")
    orgs.create.assert_not_called()
    orgs.add_member.assert_not_called()
