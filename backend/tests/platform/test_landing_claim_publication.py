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
from src.platform.access.adapters.mcp_endpoint.service import McpEndpointService
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
        "mcp_target": "project_root",
    }


class _ControlPlane:
    def __init__(
        self,
        events: list[str],
        *,
        replays: list[ProjectCreationReplay | None] | None = None,
        error: Exception | None = None,
        privacy_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.replays = list(replays or [None])
        self.error = error
        self.privacy_error = privacy_error
        self.calls: list[dict] = []

    def preflight_project_creation(self, **kwargs):
        self.events.append("preflight")
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.replays.pop(0)

    def make_initializing_project_private(self, **kwargs):
        assert kwargs["project_id"] == "project-1"
        assert kwargs["org_id"] == "org-1"
        assert kwargs["actor_user_id"] == "user-1"
        assert kwargs["operation_key"]
        self.events.append("private")
        if self.privacy_error is not None:
            raise self.privacy_error


class _S3:
    def __init__(self, events: list[str], *, fail_if_called: bool = False) -> None:
        self.events = events
        self.fail_if_called = fail_if_called

    def download_file(self, key: str) -> bytes:
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
            raise AssertionError("native landing imports must not create a Scope")

    monkeypatch.setattr("src.platform.repository_target.scope_service.ScopeService", Scope)

    endpoint_secrets: list[str] = []
    endpoint_calls: list[dict] = []

    class Endpoints:
        def create(self, **kwargs):
            endpoint_calls.append(kwargs)
            endpoint_secrets.append(kwargs["api_key"])
            return {
                "id": "endpoint-1",
                "project_id": "project-1",
                "scope_id": None,
                "path": "",
                "api_key": kwargs["api_key"],
            }

    # Exercise the real service signature and policy serialization rather than
    # accepting arbitrary keyword arguments at the endpoint-service boundary.
    endpoint_service = McpEndpointService(repository=Endpoints())
    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", lambda: endpoint_service)

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
        "private",
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
    assert scope_calls == []
    assert endpoint_calls[0]["path"] is None
    assert endpoint_calls[0]["project_id"] == "project-1"
    assert endpoint_calls[0]["accesses"] == [{"path": "", "json_path": "", "readonly": True}]
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
                "scope_id": None,
                "path": "",
                "status": "active",
                "accesses": [{"path": "", "readonly": True}],
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


@pytest.mark.asyncio
async def test_legacy_claim_metadata_cannot_be_replayed_as_a_project_root_key(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) - 60)
    metadata = _metadata(ticket_id)
    metadata.pop("mcp_target")
    events: list[str] = []

    class Credentials:
        def get_active_by_token(self, _token):
            raise AssertionError("legacy target metadata must fail before credential lookup")

    monkeypatch.setattr("src.platform.landing.service.AccessCredentialRepository", Credentials)
    service = _service(events, control_plane=_ControlPlane(events, replays=[
        ProjectCreationReplay(project=_project(), result_metadata=metadata)
    ]))

    with pytest.raises(AppException) as caught:
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "landing_claim_target_unavailable"
    assert events == ["preflight", "authorize"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"project_id": "another-project"},
    {"scope_id": "legacy-scope"},
    {"path": "Guide"},
    {"accesses": [{"path": "", "readonly": False}]},
    {"accesses": "invalid-policy"},
    {"status": "disabled"},
])
async def test_replay_rejects_changed_project_root_target_or_writable_policy(monkeypatch, changed):
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []

    class Credentials:
        def get_active_by_token(self, _token):
            return {"access_surface_id": "endpoint-1", "project_id": "project-1", "org_id": "org-1"}

    class Mcp:
        def get_endpoint(self, _endpoint_id):
            return {
                "id": "endpoint-1", "project_id": "project-1", "scope_id": None,
                "path": "", "status": "active", "accesses": [{"path": "", "readonly": True}], **changed,
            }

    monkeypatch.setattr("src.platform.landing.service.AccessCredentialRepository", Credentials)
    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", Mcp)
    service = _service(events, control_plane=_ControlPlane(events, replays=[
        ProjectCreationReplay(project=_project(), result_metadata=_metadata(ticket_id))
    ]))

    with pytest.raises(AppException) as caught:
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert caught.value.status_code == 409
    assert "api_key" not in caught.value.details


@pytest.mark.asyncio
async def test_replay_does_not_reissue_a_revoked_or_rotated_credential(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, ticket_id = _ticket(expires_at=int(time.time()) - 60)
    events: list[str] = []

    class Credentials:
        def get_active_by_token(self, _token):
            return None

    monkeypatch.setattr("src.platform.landing.service.AccessCredentialRepository", Credentials)
    service = _service(events, control_plane=_ControlPlane(events, replays=[
        ProjectCreationReplay(project=_project(), result_metadata=_metadata(ticket_id))
    ]))

    with pytest.raises(AppException) as caught:
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "landing_claim_credential_rotated"


@pytest.mark.asyncio
async def test_private_visibility_must_be_confirmed_before_content_and_endpoint_creation(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-test-secret")
    ticket, _ = _ticket(expires_at=int(time.time()) + 600)
    events: list[str] = []

    async def publish(**kwargs):
        await kwargs["initialize"](_project())
        return IdempotentProjectResult(project=_project(), replayed=False, ready=True)

    monkeypatch.setattr("src.platform.landing.service.resolve_org_id", lambda org, _user: org)
    monkeypatch.setattr("src.platform.landing.service.create_project_with_tree", publish)
    writes = _Writes(events)
    monkeypatch.setattr(
        "src.version_engine.bootstrap.dependencies.build_worker_version_engine_container",
        lambda: SimpleNamespace(write_commands=lambda: writes),
    )

    class Mcp:
        def create_endpoint(self, **_kwargs):
            return {"id": "endpoint-1"}

    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", Mcp)
    service = _service(events, control_plane=_ControlPlane(events, privacy_error=RuntimeError("private initialization failed")))

    with pytest.raises(RuntimeError, match="private"):
        await service.claim(ticket=ticket, org_id="org-1", user_id="user-1")
    assert writes.calls == []


@pytest.mark.asyncio
async def test_pdf_preview_claim_and_readonly_mcp_read_use_the_same_private_project(monkeypatch):
    """Local flow acceptance with real PDF parsing, policies and MCP handlers.

    Only persistence/publication boundaries are in memory; no production
    credentials, hosted writes or external MCP client are involved.
    """
    import re
    from contextlib import nullcontext

    import fitz
    from fastapi import HTTPException

    from src.file_operations.queries import ProductQueries
    from src.platform.access.adapters.mcp_endpoint import resolver, runtime
    from src.platform.authorization.models import ROLE_CAPABILITIES, ProjectRole
    from src.version_engine.read.tree_reader import VersionEntry

    monkeypatch.setattr(settings, "JWT_SECRET", "landing-local-flow-unit-test-secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://cloud.example")
    reference = "PDF-MCP-CHECK-20261029"
    with fitz.open() as document:
        document.new_page().insert_text(
            (72, 72), "Public PDF to MCP acceptance fixture.\nReference code: " + reference
        )
        source = document.tobytes()

    class Storage:
        objects: dict[str, bytes] = {}

        def upload_file(self, key, content, _content_type):
            assert re.fullmatch(r"[A-Za-z0-9/_.-]+", key)
            self.objects[key] = content

        def download_file(self, key):
            return self.objects[key]

    files: dict[str, bytes] = {}
    endpoint: dict = {}
    class Writes:
        async def bulk_write(self, project_id, entries, **_kwargs):
            assert project_id == "project-1"
            assert "private" in events
            files.update(entries)

    class Endpoints:
        def create(self, **kwargs):
            assert kwargs["path"] is None
            endpoint.update(kwargs, id="endpoint-1", kind="mcp", scope_id=None, status="active")
            return endpoint

    async def publish(**kwargs):
        assert kwargs["org_id"] == "org-1"
        assert kwargs["publication_mode"] == "deferred"
        await kwargs["initialize"](_project())
        return IdempotentProjectResult(project=_project(), replayed=False, ready=True)

    monkeypatch.setattr(
        "src.version_engine.bootstrap.dependencies.build_worker_version_engine_container",
        lambda: SimpleNamespace(write_commands=lambda: Writes()),
    )
    monkeypatch.setattr("src.platform.landing.service.resolve_org_id", lambda org, _user: org)
    monkeypatch.setattr("src.platform.landing.service.create_project_with_tree", publish)
    endpoints = McpEndpointService(repository=Endpoints())
    monkeypatch.setattr("src.platform.landing.service.McpEndpointService", lambda: endpoints)
    events: list[str] = []
    service = _service(events, control_plane=_ControlPlane(events), s3=Storage())

    preview = await service.preview(
        tool_kind="pdf", filename="资料.pdf", content=source, content_type="application/pdf"
    )
    assert preview["preview"]["filename"] == "资料.pdf"
    assert reference in preview["preview"]["excerpt"]
    claimed = await service.claim(ticket=preview["ticket"], org_id="org-1", user_id="user-1")
    assert claimed["project_id"] == "project-1"
    assert list(files) == ["doc/资料.md"]

    def resolve_surface(key):
        if key != claimed["mcp"]["api_key"]:
            raise HTTPException(404, detail="MCP access surface not found")
        return endpoint, {"fs_policy": {"accesses": endpoint["accesses"]}}, {"id": "credential-1"}

    monkeypatch.setattr(resolver, "_resolve_surface", resolve_surface)
    monkeypatch.setattr(resolver, "_owner_grant", lambda project, owner: SimpleNamespace(
        allows=lambda action: action in ROLE_CAPABILITIES[ProjectRole("admin")]
    ) if (project, owner) == ("project-1", "user-1") else None)
    principal = resolver.resolve_mcp_runtime(claimed["mcp"]["api_key"])
    assert principal.grant.project_id == "project-1"
    assert principal.grant.mode.value == "r"

    class Reads(ProductQueries):
        def for_grant(self, grant):
            assert grant.project_id == "project-1" and grant.mode.value == "r"
            return self

        def read_session(self, project_id):
            assert project_id == "project-1"
            return nullcontext(self)

        def _read(self, method, project_id, *args, **_kwargs):
            assert project_id == "project-1"
            if method == "get_head_commit_id":
                return "import-head"
            path = args[0]
            if method == "stat":
                if path == "doc":
                    return VersionEntry(name="doc", path="doc", type="folder")
                if path in files:
                    return VersionEntry(name="资料.md", path=path, type="markdown")
                return None
            if method == "list_dir":
                assert path == "doc"
                return [VersionEntry(name="资料.md", path="doc/资料.md", type="markdown")]
            if method == "read_file":
                return files[path]
            raise AssertionError("unexpected storage operation: " + method)

    key = claimed["mcp"]["api_key"]
    tools = runtime.list_runtime_tools(runtime.RuntimeToolsRequest(api_key=key))
    names = {tool["name"] for tool in tools["tools"]}
    assert {"fs_ls", "fs_cat"} <= names
    assert not {"fs_write", "fs_rm", "fs_mv"} & names
    reads = Reads()
    commands = SimpleNamespace(ops=reads)
    listing = await runtime.call_runtime_tool(runtime.RuntimeCallRequest(
        api_key=key, name="fs_ls", arguments={"path": "doc"}
    ), ops=reads, commands=commands)
    assert listing["structuredContent"]["entries"][0]["path"] == "doc/资料.md"
    result = await runtime.call_runtime_tool(runtime.RuntimeCallRequest(
        api_key=key, name="fs_cat", arguments={"path": "doc/资料.md"}
    ), ops=reads, commands=commands)
    assert reference in result["structuredContent"]["content_text"]
    with pytest.raises(HTTPException) as denied:
        await runtime.call_runtime_tool(runtime.RuntimeCallRequest(
            api_key=key, name="fs_write", arguments={"path": "doc/资料.md", "content": "changed"}
        ), ops=reads, commands=commands)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as unknown:
        runtime.list_runtime_tools(runtime.RuntimeToolsRequest(api_key="mcp_wrong_key"))
    assert unknown.value.status_code == 404
    monkeypatch.setattr(resolver, "_owner_grant", lambda _project, _owner: None)
    with pytest.raises(HTTPException) as revoked:
        runtime.list_runtime_tools(runtime.RuntimeToolsRequest(api_key=key))
    assert revoked.value.status_code == 403
