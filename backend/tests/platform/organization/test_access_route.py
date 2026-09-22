from __future__ import annotations

import pytest

from src.exceptions import ForbiddenException
from src.platform.auth.models import CurrentUser
from src.platform.organization.router import get_organization_access


class _Organizations:
    def __init__(self, role: str | None) -> None:
        self.role = role
        self.calls: list[tuple[str, str]] = []

    def get_my_role(self, org_id: str, user_id: str) -> str | None:
        self.calls.append((org_id, user_id))
        return self.role


def _user() -> CurrentUser:
    return CurrentUser(user_id="user-1", email="owner@example.com", role="authenticated")


@pytest.mark.parametrize(
    ("role", "can_manage_billing"),
    [("owner", True), ("admin", False), ("member", False), ("viewer", False)],
)
def test_access_route_returns_only_current_user_capability(
    role: str,
    can_manage_billing: bool,
) -> None:
    organizations = _Organizations(role)

    response = get_organization_access(
        "org-1",
        org_service=organizations,  # type: ignore[arg-type]
        current_user=_user(),
    )

    assert organizations.calls == [("org-1", "user-1")]
    assert response.data is not None
    assert response.data.model_dump() == {
        "org_id": "org-1",
        "user_id": "user-1",
        "role": role,
        "can_manage_billing": can_manage_billing,
    }


def test_access_route_denies_non_members_without_enumerating_members() -> None:
    organizations = _Organizations(None)

    with pytest.raises(ForbiddenException, match="Not a member"):
        get_organization_access(
            "org-1",
            org_service=organizations,  # type: ignore[arg-type]
            current_user=_user(),
        )

    assert organizations.calls == [("org-1", "user-1")]
