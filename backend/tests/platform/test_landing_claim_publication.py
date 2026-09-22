from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.config import settings
from src.exceptions import AppException, ErrorCode, PermissionException
from src.platform.authorization.models import ProjectAction
from src.platform.landing import tickets
from src.platform.landing.schemas import ClaimRequest
from src.platform.landing.service import LandingService, _landing_mcp_api_key
from src.platform.project.control_plane import (
    IdempotentProjectResult,
    ProjectCreationReplay,
)
from src.platform.project.models import Project


def _project(*, org_id: str = "org-1") -> Project:
    return Project(
        id="project-1",
        name="Guide Docs",
        description="Imported",
        org_id=org_id,
        created_by="user-1",
        created_at=datetime.now(UTC),
    )


def _ticket(*, expires_at: int, ticket_id: UUID | None = None) -> tuple[str, UUID]:
    ticket_id = ticket_id or uuid4()
    return (
        tickets.sign_ticket(
            {
                "tid": ticket_id.hex,
                "kind": "pdf",
                "md_key": "landing/ticket/parsed/guide.md",
                "md_name": "guide.md",
                "src_name": "guide.pdf",
                "exp": expires_at,
            }
        ),
        ticket_id,
    )


def _metadata(ticket_id: UUID) -> dict[str, object]:
    return {
        "kind": "landing-claim",
        "ticket_id": str(ticket_id),
        "tool_kind": "pdf",
        "repo": "Guide",
        "endpoint_name": "pdf-mcp",
        "credential_derivation_version": 1,
    }


class _ControlPlane:
    def __init__(
        self,
        events: list[str],
        *,
        replays: list[ProjectCreationReplay | None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.replays = list(replays or [None])
        self.error = error
        self.calls: list[dict] = []

    def preflight_project_creation(self, **kwargs):
        self.events.append("preflight")
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.replays.pop(0)


class _S3:
    def __init__(self, events: list[str], *, fail_if_called: bool = False) -> None:
        self.events = events
        self.fail_if_called = fail_if_called

    async def download_file(self, key: str) -> bytes:
        if self.fail_if_called:
            raise AssertionError("preview object must not be read during replay")
        self.events.append("download")
        assert key == "landing/ticket/parsed/guide.md"
        return b"# Guide"


class _Entitlements:
    def __init__(self, events: list[str], *, fail_if_called: bool = False) -> None:
        self.events = events
        self.fail_if_called = fail_if_called

    def enforced_limit_value(self, org_id: str, key: str):
        if self.fail_if_called:
            raise AssertionError("capacity must not be read during replay")
        self.events.append("capacity")
        assert (org_id, key) == ("org-1", "projects.max")
        return 5


class _Authorization:
    def __init__(self, events: list[str], *, allowed: bool = True) -> None:
        self.events = events
        self.allowed = allowed
        self.calls: list[tuple] = []

    def authorize(self, project_id: str, user_id: str, action: ProjectAction):
        self.events.append("authorize")
        self.calls.append((project_id, user_id, action))
        if not self.allowed:
            raise PermissionException("Project access revoked")
        return SimpleNamespace()


class _Writes:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple] = []

    async def bulk_write(self, *args, **kwargs) -> None:
        self.events.append("write")
        self.calls.append((args, kwargs))


def _service(
    events: list[str],
    *,
    control_plane: _ControlPlane,
    s3: _S3 | None = None,
    entitlements: _Entitlements | None = None,
    authorization: _Authorization | None = None,
) -> LandingService:
    return LandingService(
        s3 or _S3(events),  # type: ignore[arg-type]
        control_plane,  # type: ignore[arg-type]
        entitlements or _Entitlements(events),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        authorization or _Authorization(events),  # type: ignore[arg-type]
    )


def test_claim_request_requires_an_explicit_nonblank_organization() -> None:
    with pytest.raises(ValidationError, match="org_id"):
        ClaimRequest(ticket="signed")
    with pytest.raises(ValidationError, match="org_id"):
        ClaimRequest(ticket="signed", org_id="   ")
    assert ClaimRequest(ticket="signed", org_id=" org-1 ").org_id == "org-1"


def test_claim_request_rejects_unknown_landing_contract_version() -> None:
    assert ClaimRequest(ticket="signed", org_id="org-1").contract_version == 1
    with pytest.raises(ValidationError, match="contract_version"):
        ClaimRequest(ticket="signed", org_id="org-1", contract_version=2)


def test_ticket_signature_and_expiry_can_be_checked_separately(monkeypatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, _ = _ticket(expires_at=int(time.time()) - 60)

    body = tickets.verify_ticket_signature(ticket)

    assert body["kind"] == "pdf"
    with pytest.raises(tickets.TicketError, match="expired"):
        tickets.require_unexpired(body)
    with pytest.raises(tickets.TicketError, match="expired"):
        tickets.verify_ticket(ticket)


@pytest.mark.asyncio
async def test_new_claim_preflights_before_source_and_persists_stable_replay_facts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://cloud.example")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) + 600)
    events: list[str] = []
    control_plane = _ControlPlane(events)
    writes = _Writes(events)
    container = SimpleNamespace(write_commands=lambda: writes)
    monkeypatch.setattr(
        "src.version_engine.bootstrap.dependencies.build_worker_version_engine_container",
        lambda: container,
    )
    monkeypatch.setattr(
        "src.platform.landing.service.get_tool_spec",
        lambda _kind: SimpleNamespace(
            kind="pdf",
            scope_path="Guide",
            name_suffix=" Docs",
            readonly=True,
        ),
    )

    org_calls: list[tuple[str | None, str]] = []

    def resolve_org(requested, user_id):
        org_calls.append((requested, user_id))
        return requested

    monkeypatch.setattr("src.platform.landing.service.resolve_org_id", resolve_org)

    scope_calls: list[dict] = []

    class Scope:
        def create(self, **kwargs):
            scope_calls.append(kwargs)
            return SimpleNamespace(id="scope-1")

    monkeypatch.setattr("src.platform.landing.service.ScopeService", Scope)

    endpoint_secrets: list[str] = []

    class Mcp:
        def create_endpoint(self, **kwargs):
            endpoint_secrets.append(kwargs["api_key"])
            return {
                "id": "endpoint-1",
                "project_id": "project-1",
                "path": "Guide",
                "api_key": kwargs["api_key"],
            }

    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", Mcp)

    publication_calls: list[dict] = []

    async def publish(**kwargs):
        events.append("create")
        publication_calls.append(kwargs)
        await kwargs["initialize"](_project())
        return IdempotentProjectResult(project=_project(), replayed=False, ready=True)

    monkeypatch.setattr("src.platform.landing.service.create_project_with_tree", publish)
    authorization = _Authorization(events)
    service = _service(
        events,
        control_plane=control_plane,
        authorization=authorization,
    )

    result = await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert events == [
        "preflight",
        "download",
        "capacity",
        "create",
        "write",
        "authorize",
    ]
    assert org_calls == [("org-1", "user-1")]
    assert result["mcp"]["api_key"] == endpoint_secrets[0]
    assert result["mcp"]["endpoint_id"] == "endpoint-1"
    assert authorization.calls == [("project-1", "user-1", ProjectAction.PROJECT_READ)]
    request = publication_calls[0]
    assert request["operation_key"] == str(ticket_id)
    assert request["request_fingerprint"] == {
        "kind": "landing-claim-request",
        "version": 1,
        "signed_ticket": ticket,
        "org_id": "org-1",
    }
    assert request["result_metadata"] == _metadata(ticket_id)
    assert request["source_fingerprint"] == {
        "kind": "landing-claim",
        "ticket_id": str(ticket_id),
        "tool_kind": "pdf",
        "source_key": "landing/ticket/parsed/guide.md",
        "content_sha256": hashlib.sha256(b"# Guide").hexdigest(),
    }
    assert len(scope_calls) == 1
    assert len(writes.calls) == 1


@pytest.mark.asyncio
async def test_expired_completed_claim_replays_without_preview_object(monkeypatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://cloud.example")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []
    replay = ProjectCreationReplay(
        project=_project(),
        result_metadata=_metadata(ticket_id),
    )
    control_plane = _ControlPlane(events, replays=[replay])
    authorization = _Authorization(events)
    expected_key = _landing_mcp_api_key(ticket_id=str(ticket_id), user_id="user-1")

    class Credentials:
        def get_active_by_token(self, raw_token: str):
            events.append("credential")
            assert raw_token == expected_key
            return {
                "access_surface_id": "endpoint-1",
                "project_id": "project-1",
                "org_id": "org-1",
            }

    class Mcp:
        def get_endpoint(self, endpoint_id: str):
            events.append("endpoint")
            assert endpoint_id == "endpoint-1"
            return {
                "id": endpoint_id,
                "project_id": "project-1",
                "path": "Guide",
            }

    monkeypatch.setattr("src.platform.landing.service.AccessCredentialRepository", Credentials)
    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", Mcp)
    monkeypatch.setattr(
        "src.platform.landing.service.resolve_org_id",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("completed replay must not resolve a default organization")
        ),
    )
    service = _service(
        events,
        control_plane=control_plane,
        s3=_S3(events, fail_if_called=True),
        entitlements=_Entitlements(events, fail_if_called=True),
        authorization=authorization,
    )

    result = await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert events == ["preflight", "authorize", "credential", "endpoint"]
    assert result["mcp"] == {
        "server_url": "https://cloud.example/api/v1/mcp/proxy",
        "api_key": expected_key,
        "endpoint_id": "endpoint-1",
    }


@pytest.mark.asyncio
async def test_expired_new_claim_is_rejected_before_preview_download(monkeypatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, _ = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []
    monkeypatch.setattr(
        "src.platform.landing.service.resolve_org_id",
        lambda requested, _user: requested,
    )
    service = _service(
        events,
        control_plane=_ControlPlane(events),
        s3=_S3(events, fail_if_called=True),
    )

    with pytest.raises(tickets.TicketError, match="expired"):
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert events == ["preflight"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AppException(
            code=ErrorCode.VERSION_CONFLICT,
            status_code=409,
            message="in progress",
            details={"code": "project_publication_in_progress"},
        ),
        AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="org-bound request changed",
            details={"code": "idempotency_key_reused"},
        ),
    ],
)
async def test_preflight_conflict_or_in_progress_never_reads_expiry_or_source(
    monkeypatch,
    error: AppException,
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, _ = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []
    service = _service(
        events,
        control_plane=_ControlPlane(events, error=error),
        s3=_S3(events, fail_if_called=True),
        entitlements=_Entitlements(events, fail_if_called=True),
    )

    with pytest.raises(AppException) as caught:
        await service.claim(ticket=ticket, org_id="org-2", user_id="user-1")

    assert caught.value.details == error.details
    assert events == ["preflight"]


@pytest.mark.asyncio
async def test_completed_replay_rechecks_current_project_access_before_secret(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []
    replay = ProjectCreationReplay(
        project=_project(),
        result_metadata=_metadata(ticket_id),
    )

    class Credentials:
        def get_active_by_token(self, _raw_token: str):
            raise AssertionError("secret state must not be read after authorization loss")

    monkeypatch.setattr("src.platform.landing.service.AccessCredentialRepository", Credentials)
    service = _service(
        events,
        control_plane=_ControlPlane(events, replays=[replay]),
        s3=_S3(events, fail_if_called=True),
        entitlements=_Entitlements(events, fail_if_called=True),
        authorization=_Authorization(events, allowed=False),
    )

    with pytest.raises(PermissionException):
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert events == ["preflight", "authorize"]
