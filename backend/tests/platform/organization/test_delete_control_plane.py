from __future__ import annotations

import pytest

from src.exceptions import AppException, ForbiddenException, NotFoundException
from src.platform.organization.service import OrganizationService


class _Repository:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def delete_empty_control_plane(self, org_id: str, actor_user_id: str) -> dict:
        self.calls.append((org_id, actor_user_id))
        return {"outcome": self.outcome}

    def delete(self, *_args, **_kwargs):
        raise AssertionError("direct Organization DELETE must never be used")

    def list_by_user(self, *_args, **_kwargs):
        raise AssertionError("the last-Organization proof belongs in the atomic RPC")

    def get_member(self, *_args, **_kwargs):
        raise AssertionError("the final owner check belongs in the atomic RPC")


def _delete(outcome: str) -> _Repository:
    repository = _Repository(outcome)
    OrganizationService(repository).delete("org-1", "owner-1")  # type: ignore[arg-type]
    return repository


def test_delete_uses_only_the_atomic_empty_organization_control_plane() -> None:
    repository = _delete("deleted")

    assert repository.calls == [("org-1", "owner-1")]


def test_delete_refuses_to_cascade_projects_around_the_cleanup_journal() -> None:
    with pytest.raises(AppException) as raised:
        _delete("organization_not_empty")

    assert raised.value.status_code == 409
    assert raised.value.details == {"reason": "organization_not_empty"}
    assert "Delete every project" in raised.value.message


def test_delete_preserves_the_users_only_organization() -> None:
    with pytest.raises(AppException, match="only organization") as raised:
        _delete("only_organization")

    assert raised.value.status_code == 403


def test_delete_waits_for_every_project_cleanup_job_to_complete() -> None:
    with pytest.raises(AppException) as raised:
        _delete("organization_deletion_in_progress")

    assert raised.value.status_code == 409
    assert raised.value.details == {
        "reason": "organization_deletion_in_progress"
    }


def test_delete_maps_final_atomic_authorization_results() -> None:
    with pytest.raises(ForbiddenException, match="Only owner"):
        _delete("forbidden")

    with pytest.raises(NotFoundException, match="Organization not found"):
        _delete("not_found")


def test_delete_fails_closed_on_an_unrecognized_control_plane_response() -> None:
    with pytest.raises(AppException) as raised:
        _delete("unexpected")

    assert raised.value.status_code == 500
