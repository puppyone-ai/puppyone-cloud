"""A hosted deployment refuses to start unless every Workspace is proven isolated."""

import pytest
from pydantic import ValidationError

from src.config import Settings


def hosted(**values):
    # Isolate this validator from unrelated hosted deployment prerequisites.
    return Settings.model_construct(
        APP_ENV="production",
        SANDBOX_TYPE="e2b",
        E2B_API_KEY="",
        SANDBOX_REAPER_ENABLED=True,
        **values,
    )


def test_an_unnamed_environment_refuses_to_start(monkeypatch):
    # Every hosted check depends on this value, so it can never be assumed.
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError, match="APP_ENV"):
        Settings(_env_file=None)


def test_fly_only_hosted_agent_requires_its_own_credentials():
    value = hosted(CLOUD_AGENT_SANDBOX_PROVIDER="fly")
    with pytest.raises(ValueError, match="CLOUD_AGENT_FLY_APP"):
        value.enforce_hosted_sandbox_safety()
    value.CLOUD_AGENT_FLY_APP, value.CLOUD_AGENT_FLY_TOKEN = "test-app", "test-token"
    with pytest.raises(ValueError, match="CLOUD_AGENT_FLY_ORG"):
        value.enforce_hosted_sandbox_safety()
    value.CLOUD_AGENT_FLY_ORG = "test-org"
    with pytest.raises(ValueError, match="pinned by digest"):
        value.enforce_hosted_sandbox_safety()
    value.CLOUD_AGENT_FLY_IMAGE = "registry.fly.io/test-app@sha256:" + "a" * 64
    assert value.enforce_hosted_sandbox_safety() is value
    assert value.E2B_API_KEY == ""
    value.SANDBOX_REAPER_ENABLED = False
    with pytest.raises(ValueError, match="SANDBOX_REAPER_ENABLED"):
        value.enforce_hosted_sandbox_safety()
    value.SANDBOX_REAPER_ENABLED = True
    value.SANDBOX_TYPE = "docker"
    with pytest.raises(ValueError, match="no Docker fallback"):
        value.enforce_hosted_sandbox_safety()


@pytest.mark.parametrize("provider", ["", "auto", "e2b", "sprites", "docker"])
@pytest.mark.parametrize("role", ["api", "agent_worker", "ssh_gateway"])
def test_a_workspace_role_starts_only_on_a_provider_with_a_dedicated_network(role, provider):
    with pytest.raises(ValueError):
        hosted(SERVICE_ROLE=role, CLOUD_AGENT_SANDBOX_PROVIDER=provider).enforce_hosted_sandbox_safety()


@pytest.mark.parametrize("existing", ["sprites", "e2b", "docker", "fly,sprites"])
def test_an_existing_binding_cannot_keep_an_unisolated_provider(existing):
    with pytest.raises(ValueError, match="must be Fly"):
        hosted(
            CLOUD_AGENT_SANDBOX_PROVIDER="fly", CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS=existing
        ).enforce_hosted_sandbox_safety()


@pytest.mark.parametrize("role", ["upload_worker", "import_worker", "synchronize_worker", "mcp_server"])
def test_roles_without_workspaces_need_no_workspace_credentials(role):
    for provider in ("", "fly"):
        value = hosted(SERVICE_ROLE=role, CLOUD_AGENT_SANDBOX_PROVIDER=provider)
        assert value.enforce_hosted_sandbox_safety() is value
    with pytest.raises(ValueError, match="shared-kernel Docker"):
        hosted(SERVICE_ROLE=role, CLOUD_AGENT_SANDBOX_PROVIDER="docker").enforce_hosted_sandbox_safety()
