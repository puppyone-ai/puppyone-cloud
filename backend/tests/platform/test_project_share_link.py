"""Share-link join contract.

``join_project_via_share_token`` fails closed with SQLSTATE ``42501`` when the
recipient is not already an ``org_members`` row for the project's organization.
That is a product boundary, not a server fault, and the share landing page can
only explain it if the API says so. Before this contract the PostgREST error
escaped to the generic handler and the recipient saw an unexplained 500 on a
link that had been pasted to them.
"""

from __future__ import annotations

import pytest

from src.exceptions import AppException, ErrorCode
from src.platform.project.service import ProjectService


class _Projects:
    """The share-join path never touches the project repository."""


class _Memberships:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def join_with_share_token(self, share_token: str, user_id: str):
        self.calls.append((share_token, user_id))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _PostgrestError(Exception):
    """Shape of ``postgrest.exceptions.APIError`` that matters to us."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _service(outcome) -> tuple[ProjectService, _Memberships]:
    memberships = _Memberships(outcome)
    return ProjectService(_Projects(), memberships), memberships


def test_join_returns_membership_row() -> None:
    row = {
        "project_id": "project-1",
        "project_name": "Atlas",
        "role": "viewer",
        "newly_joined": True,
    }
    service, memberships = _service(row)

    assert service.join_via_share_token("tok-1", "user-2") == row
    assert memberships.calls == [("tok-1", "user-2")]


def test_rotated_or_unknown_token_is_not_found() -> None:
    service, _ = _service(None)

    with pytest.raises(AppException) as excinfo:
        service.join_via_share_token("stale", "user-2")

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_out_of_organization_recipient_is_forbidden_not_a_server_error() -> None:
    service, _ = _service(
        _PostgrestError("42501", "share recipient must belong to the organization")
    )

    with pytest.raises(AppException) as excinfo:
        service.join_via_share_token("tok-1", "outsider")

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    assert "organization" in excinfo.value.message


def test_denial_is_recognized_without_a_sqlstate_attribute() -> None:
    """Library bumps must not silently turn this back into a 500."""
    service, _ = _service(RuntimeError("share recipient must belong to the organization"))

    with pytest.raises(AppException) as excinfo:
        service.join_via_share_token("tok-1", "outsider")

    assert excinfo.value.status_code == 403


def test_unrelated_database_failure_still_propagates() -> None:
    service, _ = _service(_PostgrestError("57014", "statement timeout"))

    with pytest.raises(_PostgrestError):
        service.join_via_share_token("tok-1", "user-2")
