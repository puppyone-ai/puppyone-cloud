"""A Fly-only Agent deployment must not enable Docker or require unused E2B."""

import pytest

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


def test_fly_only_hosted_agent_requires_its_own_credentials():
    value = hosted(CLOUD_AGENT_SANDBOX_PROVIDER="fly")
    with pytest.raises(ValueError, match="CLOUD_AGENT_FLY_APP"):
        value.enforce_hosted_sandbox_safety()
    value.CLOUD_AGENT_FLY_APP, value.CLOUD_AGENT_FLY_TOKEN = "test-app", "test-token"
    with pytest.raises(ValueError, match="pinned by digest"):
        value.enforce_hosted_sandbox_safety()
    value.CLOUD_AGENT_FLY_IMAGE = "registry.fly.io/test-app@sha256:" + "a" * 64
    assert value.enforce_hosted_sandbox_safety() is value
    value.SANDBOX_TYPE = "docker"
    with pytest.raises(ValueError, match="no Docker fallback"):
        value.enforce_hosted_sandbox_safety()


@pytest.mark.parametrize("provider", ["auto", "e2b", "docker"])
def test_hosted_agent_cannot_fall_back_to_docker(provider):
    with pytest.raises(ValueError):
        hosted(CLOUD_AGENT_SANDBOX_PROVIDER=provider).enforce_hosted_sandbox_safety()
