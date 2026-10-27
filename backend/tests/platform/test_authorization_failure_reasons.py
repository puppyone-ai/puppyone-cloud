"""Authorization outages stay fail-closed and expose only actionable categories."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from postgrest.exceptions import APIError
from starlette.requests import Request

from src.exception_handler import app_exception_handler
from src.exceptions import AppException
from src.platform.authorization.service import AuthorizationService


@pytest.mark.parametrize("entrypoint", ["project", "catalog", "ids"])
@pytest.mark.parametrize(
    ("cause", "reason"),
    [
        (httpx.ConnectError("https://secret@private-db"), "project_authorization_unreachable"),
        (httpx.ReadError("private response"), "project_authorization_unreachable"),
        (httpx.ConnectTimeout("private endpoint"), "project_authorization_timeout"),
        (httpx.ReadTimeout("private query"), "project_authorization_timeout"),
        (TimeoutError("private deadline"), "project_authorization_timeout"),
        (RuntimeError("private SQL"), "project_authorization_unavailable"),
        (APIError({"code": "PGRST202", "message": "private RPC"}), "database_schema_outdated"),
        (APIError({"code": "PGRST205", "message": "private table"}), "database_schema_outdated"),
        (APIError({"code": "42883", "message": "private function"}), "database_schema_outdated"),
        (APIError({"code": "42P01", "message": "private relation"}), "database_schema_outdated"),
        (
            APIError({"code": "42501", "message": "private service key"}),
            "project_authorization_unavailable",
        ),
    ],
)
def test_dependency_failures_preserve_reason_across_public_error_envelope(
    entrypoint, cause, reason
):
    repository = Mock()
    repository.load_project_facts.side_effect = cause
    repository.load_project_facts_batch.side_effect = cause
    service = AuthorizationService(repository)
    with pytest.raises(AppException) as caught:
        if entrypoint == "project":
            service.resolve_project_grant("project", "user")
        elif entrypoint == "catalog":
            service.filter_accessible([SimpleNamespace(id="project")], "user")
        else:
            service.accessible_project_ids(["project"], "user")

    response = app_exception_handler(Request({"type": "http"}), caught.value)
    payload = json.loads(response.body)
    assert response.status_code == 503  # No empty result, false denial or stale grant.
    assert payload["data"] == {"code": reason, "retryable": True}
    assert int(response.headers["Retry-After"]) > 0
    assert "private" not in response.body.decode()
    assert "secret" not in response.body.decode()
    assert (
        repository.load_project_facts.call_count + repository.load_project_facts_batch.call_count
        == 1
    )
