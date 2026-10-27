"""Shared authority boundaries independent of provider and protocol transport."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncssh
import pytest
from pydantic import ValidationError

from src.platform.access.adapters.ssh.hosted import HostedSshAccess
from src.platform.access.credentials import normalize_ssh_public_key
from src.platform.access.lifecycle_router import verified_user
from src.platform.access.lifecycle_schemas import CredentialRenewal, InvitationCreate
from src.platform.access.sessions import AccessSessions
from src.platform.auth.models import CurrentUser
from src.platform.authorization.service import validate_access_shape
from src.platform.workspace.errors import WorkspaceError


@pytest.mark.parametrize(
    "target,caps,channels",
    [
        ("project_root", ["repository.read"], ["ssh"]),
        ("workspace", ["workspace.read"], ["ssh"]),
        ("workspace", ["workspace.read", "workspace.shell"], ["ssh"]),
        ("workspace", ["workspace.read", "workspace.write", "workspace.shell"], ["git"]),
        ("project_root", ["members.manage"], ["git"]),
        ("project_root", ["repository.write"], ["git"]),
    ],
)
def test_capabilities_cannot_widen_into_another_plane(target, caps, channels):
    with pytest.raises(ValueError):
        validate_access_shape(target, caps, channels)


def test_private_key_is_never_accepted_as_a_verifier():
    key = asyncssh.generate_private_key("ssh-ed25519")
    assert normalize_ssh_public_key(key.export_public_key()).startswith("ssh-ed25519 ")
    with pytest.raises(ValueError):
        normalize_ssh_public_key(key.export_private_key())


def test_anonymous_identity_cannot_register_a_durable_agent():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        verified_user(CurrentUser(user_id=str(uuid4()), role="authenticated", is_anonymous=True))
    assert error.value.status_code == 403


def test_native_shell_request_rejects_subdirectory_and_readonly_semantics():
    values = dict(
        id=str(uuid4()),
        secret="pai_" + "x" * 43,
        target_kind="workspace",
        workbench_id=str(uuid4()),
        capabilities=["workspace.read", "workspace.write", "workspace.shell"],
        channels=["ssh"],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        InvitationCreate(**values, path_prefix="safe")
    assert InvitationCreate(**values).target_kind == "workspace"
    values["capabilities"] = ["workspace.read"]
    with pytest.raises(ValidationError):
        InvitationCreate(**values)


def test_renewal_requires_separate_protocol_proof():
    base = dict(
        request_id=str(uuid4()),
        grant_id=str(uuid4()),
        channel="git",
        next_secret="par_" + "x" * 43,
        credential_secret="mcp_" + "y" * 43,
        surface_id="surface",
    )
    with pytest.raises(ValidationError):
        CredentialRenewal(**base)
    base["credential_secret"] = "git_" + "y" * 43
    assert CredentialRenewal(**base).channel == "git"


async def test_managed_ssh_renews_after_ticket_expiry_only_with_current_parent():
    identity = str(uuid4())
    ticket = dict(
        id=identity,
        user_id="owner",
        project_id="project",
        workbench_id="workspace",
        generation="generation",
        access_grant_id="grant",
        used_at="used",
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    authority = SimpleNamespace(authorize=AsyncMock())
    connections = SimpleNamespace(authorize=AsyncMock())
    service = HostedSshAccess(
        object(),
        grants=SimpleNamespace(get=lambda _: ticket),
        connections=connections,
        authority=authority,
        sessions=object(),
        config=object(),
    )
    assert await service.validate(identity) == ticket
    assert authority.authorize.call_args.kwargs["session"] == identity
    authority.authorize.side_effect = PermissionError("revoked")
    # A revoked parent is an expected ending the transport can explain.
    for unused in (False, True):
        with pytest.raises(WorkspaceError) as denied:
            await service.validate(identity, unused=unused)
        assert denied.value.code == "access_denied"


async def test_failed_cleanup_does_not_starve_other_sessions():
    records = [{"id": "failed"}, {"id": "healthy"}]
    sessions = AccessSessions(SimpleNamespace(call=lambda *_a, **_kw: records))
    connections = SimpleNamespace(cleanup=AsyncMock(side_effect=[TimeoutError(), None]))
    await sessions.reap(connections)
    assert connections.cleanup.await_count == 2


def test_mixed_version_rollout_cannot_issue_new_credentials(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.config import settings
    from src.platform.access.lifecycle_router import router

    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", False)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/v1/access/credentials/renew", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "access_lifecycle_not_enabled"
