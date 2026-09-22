from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_self_hosted_defaults_have_no_puppypay_dependency() -> None:
    value = settings()
    assert value.ENTITLEMENTS_MODE == "disabled"
    assert value.BILLING_UI_ENABLED is False
    assert value.BILLING_WRITES_ENABLED is False
    assert value.PUPPYPAY_BASE_URL == ""


def test_billing_ui_requires_an_explicit_control_plane_url() -> None:
    with pytest.raises(ValidationError, match="PUPPYPAY_BASE_URL"):
        settings(BILLING_UI_ENABLED=True)

    with pytest.raises(ValidationError, match="PUPPYPAY_INTERNAL_API_SECRET"):
        settings(
            ENTITLEMENTS_MODE="db",
            BILLING_UI_ENABLED=True,
            PUPPYPAY_BASE_URL="http://localhost:8100",
        )


def test_writes_require_ui_db_projection_and_service_secret() -> None:
    with pytest.raises(ValidationError, match="ENTITLEMENTS_MODE"):
        settings(
            BILLING_UI_ENABLED=True,
            BILLING_WRITES_ENABLED=True,
            PUPPYPAY_BASE_URL="http://localhost:8100",
        )
    with pytest.raises(ValidationError, match="PUPPYPAY_INTERNAL_API_SECRET"):
        settings(
            ENTITLEMENTS_MODE="db",
            BILLING_UI_ENABLED=True,
            BILLING_WRITES_ENABLED=True,
            PUPPYPAY_BASE_URL="http://localhost:8100",
        )
    with pytest.raises(ValidationError, match="BILLING_UI_ENABLED"):
        settings(
            ENTITLEMENTS_MODE="db",
            BILLING_WRITES_ENABLED=True,
            PUPPYPAY_BASE_URL="http://localhost:8100",
            PUPPYPAY_INTERNAL_API_SECRET="s" * 32,
        )


def test_hosted_billing_rejects_plain_http_control_plane() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(
            APP_ENV="staging",
            SKIP_AUTH=False,
            ENTITLEMENTS_MODE="db",
            BILLING_UI_ENABLED=True,
            PUPPYPAY_BASE_URL="http://pay.internal",
            PUPPYPAY_INTERNAL_API_SECRET="s" * 32,
        )


def test_hosted_required_mode_requires_service_credential() -> None:
    with pytest.raises(ValidationError, match="PUPPYPAY_INTERNAL_API_SECRET"):
        settings(
            APP_ENV="staging",
            SKIP_AUTH=False,
            BILLING_ENFORCEMENT="required",
            ENTITLEMENTS_MODE="db",
            PUPPYPAY_BASE_URL="https://pay.internal",
        )


def test_hosted_db_projection_requires_control_plane_even_before_enforcement() -> None:
    with pytest.raises(ValidationError, match="PUPPYPAY_BASE_URL"):
        settings(
            APP_ENV="staging",
            SKIP_AUTH=False,
            ENTITLEMENTS_MODE="db",
        )


def test_shadow_enforcement_cannot_run_without_an_entitlement_source() -> None:
    with pytest.raises(ValidationError, match="ENTITLEMENTS_MODE"):
        settings(BILLING_ENFORCEMENT="shadow")


def test_hosted_puppypay_credential_is_not_the_general_internal_secret() -> None:
    shared = "s" * 32
    with pytest.raises(ValidationError, match="must be distinct"):
        settings(
            APP_ENV="staging",
            SKIP_AUTH=False,
            ENTITLEMENTS_MODE="db",
            PUPPYPAY_BASE_URL="https://pay.internal",
            PUPPYPAY_INTERNAL_API_SECRET=shared,
            INTERNAL_API_SECRET=shared,
        )


def test_runtime_timeout_cannot_exceed_reserved_standard_units() -> None:
    with pytest.raises(ValidationError, match="RUNTIME_AGENT_TIMEOUT_SECONDS"):
        settings(RUNTIME_AGENT_MAX_UNITS=1, RUNTIME_AGENT_TIMEOUT_SECONDS=61)


def test_runtime_recovery_fence_outlives_transport_retries() -> None:
    with pytest.raises(ValidationError, match="RUNTIME_BILLING_RECOVERY_RETRY_SECONDS"):
        settings(
            PUPPYPAY_TIMEOUT_SECONDS=20,
            RUNTIME_BILLING_RECOVERY_RETRY_SECONDS=30,
        )


def test_hosted_replica_local_derived_storage_must_be_ephemeral() -> None:
    value = settings(HOST_DERIVED_STORAGE_MODE="persistent")
    value.APP_ENV = "production"

    with pytest.raises(ValueError, match="ephemeral derived storage"):
        value.enforce_host_derived_storage_ephemerality()

    value.HOST_DERIVED_STORAGE_MODE = "ephemeral"
    assert value.enforce_host_derived_storage_ephemerality() is value
