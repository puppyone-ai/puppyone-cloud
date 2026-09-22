from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.billing.seats import is_billable_project_role
from src.platform.project.models import Project
from src.platform.project.service import ProjectService


class _Projects:
    def get_by_id(self, project_id: str) -> Project | None:
        if project_id != "project-1":
            return None
        return Project(
            id=project_id,
            name="Project",
            org_id="org-1",
            created_at=datetime.now(UTC),
        )


class _Memberships:
    def __init__(self, *, role: str | None = None, other_billable: bool = False) -> None:
        self.role = role
        self.other_billable = other_billable
        self.mutations: list[tuple[str, str]] = []

    def get(self, project_id: str, user_id: str):
        assert (project_id, user_id) == ("project-1", "user-2")
        if self.role is None:
            return None
        return {"project_id": project_id, "user_id": user_id, "role": self.role}

    def is_billable_organization_member(self, org_id: str, user_id: str) -> bool:
        assert (org_id, user_id) == ("org-1", "user-2")
        return self.other_billable or is_billable_project_role(self.role or "")

    def add(self, project_id: str, user_id: str, role: str, actor_user_id: str):
        assert (project_id, user_id, actor_user_id) == (
            "project-1",
            "user-2",
            "owner-1",
        )
        self.role = role
        self.mutations.append(("add", role))
        return {"role": role}

    def update_role(self, project_id: str, user_id: str, role: str, actor_user_id: str):
        assert (project_id, user_id, actor_user_id) == (
            "project-1",
            "user-2",
            "owner-1",
        )
        self.role = role
        self.mutations.append(("update", role))
        return {"role": role}

    def remove(self, project_id: str, user_id: str, actor_user_id: str) -> bool:
        assert (project_id, user_id, actor_user_id) == (
            "project-1",
            "user-2",
            "owner-1",
        )
        self.role = None
        self.mutations.append(("remove", ""))
        return True


class _Seats:
    def __init__(self, *, deny_activation: bool = False) -> None:
        self.deny_activation = deny_activation
        self.activations: list[dict] = []
        self.deactivations: list[dict] = []

    def ensure_member_activation(self, **values):
        self.activations.append(values)
        if self.deny_activation:
            raise AppException(
                code=ErrorCode.FORBIDDEN,
                status_code=409,
                message="Seat required",
            )

    def record_member_deactivation(self, **values):
        self.deactivations.append(values)


def test_project_role_policy_maps_capabilities_not_labels() -> None:
    assert is_billable_project_role("admin")
    assert is_billable_project_role("editor")
    assert not is_billable_project_role("viewer")
    assert not is_billable_project_role("member")


def test_first_project_write_capability_is_gated_before_mutation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    memberships = _Memberships()
    seats = _Seats(deny_activation=True)
    service = ProjectService(_Projects(), memberships, seat_billing_service=seats)

    with pytest.raises(AppException, match="Seat required"):
        service.add_project_member(
            "project-1",
            "user-2",
            "editor",
            granted_by="owner-1",
        )

    assert memberships.mutations == []
    assert seats.activations[0]["grants_billable_capability"] is True


def test_last_project_write_capability_records_seat_decrease(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    memberships = _Memberships(role="editor")
    seats = _Seats()
    service = ProjectService(_Projects(), memberships, seat_billing_service=seats)

    service.update_project_member_role(
        "project-1",
        "user-2",
        "viewer",
        actor_user_id="owner-1",
    )

    assert memberships.mutations == [("update", "viewer")]
    assert seats.deactivations[0]["was_billable"] is True


def test_removing_one_of_multiple_write_grants_does_not_reduce_seats(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    memberships = _Memberships(role="editor", other_billable=True)
    seats = _Seats()
    service = ProjectService(_Projects(), memberships, seat_billing_service=seats)

    service.remove_project_member(
        "project-1",
        "user-2",
        actor_user_id="owner-1",
    )

    assert memberships.mutations == [("remove", "")]
    assert seats.deactivations == []
