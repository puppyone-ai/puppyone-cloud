from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.config import settings
from src.exceptions import AppException
from src.platform.entitlements.models import EntitlementSnapshot
from src.platform.entitlements.service import EntitlementService


class _EmptyEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return None


class _ValidEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return EntitlementSnapshot(
            org_id=org_id,
            plan_id="free",
            status="free",
            source="puppypay",
            seat_quantity=1,
            catalog_version="launch-v1",
            source_revision=1,
            effective_at=datetime(2026, 7, 14, tzinfo=UTC),
            payload_hash="a" * 64,
            entitlements={
                "features": {
                    "access_surface.agent": False,
                    "access_surface.mcp": False,
                    "access_surface.sandbox": False,
                    "automation.hosted": False,
                    "remote_workspace.create": False,
                    "scope_sandbox.connect": False,
                },
                "limits": {
                    "projects.max": 1,
                    "repo_scopes.max_per_project": 2,
                    "storage.max_bytes": 500 * 1024**2,
                    "upload.max_single_file_bytes": 50 * 1024**2,
                    "seats.purchased": 1,
                    "runtime.included_units": 0,
                },
                "allow": {"access_surface_kinds": ["git_remote", "cli", "direct"]},
            },
        )


def test_missing_hosted_snapshot_fails_closed_without_a_local_free_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_EmptyEntitlementRepository()).get_snapshot("org-1")

    assert caught.value.status_code == 503
    assert caught.value.details["code"] == "entitlement_snapshot_missing"
    assert caught.value.details["retryable"] is True


def test_general_entitlement_rollout_does_not_enforce_before_required(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    service = EntitlementService(repository=_ValidEntitlementRepository())

    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", "disabled", raising=False)
    service.require_capacity("org-1", "projects.max", current_count=1)
    assert service.enforced_limit_value("org-1", "upload.max_single_file_bytes") is None

    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", "shadow", raising=False)
    service.require_capacity("org-1", "projects.max", current_count=1)
    service.require_feature("org-1", "automation.hosted")
    assert service.enforced_limit_value("org-1", "upload.max_single_file_bytes") is None

    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", "required", raising=False)
    with pytest.raises(AppException) as caught:
        service.require_capacity("org-1", "projects.max", current_count=1)
    assert caught.value.status_code == 403
    assert service.enforced_limit_value("org-1", "upload.max_single_file_bytes") == 50 * 1024**2


def test_shadow_mode_does_not_block_when_snapshot_is_temporarily_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", "shadow", raising=False)
    service = EntitlementService(repository=_EmptyEntitlementRepository())

    service.require_capacity("org-1", "projects.max", current_count=100)
    service.require_feature("org-1", "automation.hosted")
    service.require_allowed("org-1", "access_surface_kinds", "sandbox")
    assert service.enforced_limit_value("org-1", "upload.max_single_file_bytes") is None


class _PlusPartialEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return EntitlementSnapshot(
            org_id=org_id,
            plan_id="plus",
            status="active",
            source="puppypay",
            entitlements={"features": {"access_surface.mcp": True}},
        )


def test_incomplete_hosted_paid_snapshot_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_PlusPartialEntitlementRepository()).get_snapshot("org-1")

    assert caught.value.status_code == 503
    assert caught.value.details == {"code": "entitlement_snapshot_invalid"}


class _LegacyHostedEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return EntitlementSnapshot(
            org_id=org_id,
            plan_id="free",
            status="free",
            source="local",
            source_revision=0,
            entitlements={},
        )


def test_hosted_db_rejects_legacy_or_local_snapshot_rows(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_LegacyHostedEntitlementRepository()).get_snapshot("org-1")

    assert caught.value.status_code == 503
    assert caught.value.details == {"code": "entitlement_snapshot_invalid"}


class _UnknownSchemaEntitlementRepository(_ValidEntitlementRepository):
    def get_by_org_id(self, org_id: str):
        return super().get_by_org_id(org_id).model_copy(update={"schema_version": "2.0"})


def test_hosted_db_rejects_unknown_snapshot_schema_major(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_UnknownSchemaEntitlementRepository()).get_snapshot("org-1")

    assert caught.value.status_code == 503
    assert caught.value.details == {"code": "entitlement_snapshot_invalid"}


class _ExpiredEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return EntitlementSnapshot(
            org_id=org_id,
            plan_id="plus",
            status="active",
            source="puppypay",
            seat_quantity=1,
            source_revision=3,
            payload_hash="a" * 64,
            effective_until=datetime.now(UTC) - timedelta(seconds=1),
            entitlements={},
        )


def test_expired_hosted_snapshot_fails_closed_without_downgrading_to_free(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_ExpiredEntitlementRepository()).get_snapshot("org-1")

    assert caught.value.status_code == 503
    assert caught.value.details["code"] == "entitlement_snapshot_expired"
    assert caught.value.details["retryable"] is True
