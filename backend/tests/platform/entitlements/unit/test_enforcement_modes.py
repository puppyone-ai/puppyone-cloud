from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.config import settings
from src.exceptions import AppException
from src.platform.entitlements import service as service_module
from src.platform.entitlements.service import EntitlementService
from src.utils.request_context import entitlement_snapshot_cache_var
from tests.platform.entitlements.support import (
    _EmptyEntitlementRepository,
    _ValidEntitlementRepository,
)


class _Team(_ValidEntitlementRepository):
    """A paid Team space: two seats, two projects, every feature, limits of each kind."""

    def __init__(self) -> None:
        self.reads = 0

    def get_by_org_id(self, org_id: str):
        self.reads += 1
        free = super().get_by_org_id(org_id)
        entitlements = json.loads(json.dumps(free.entitlements))
        entitlements["features"] = {key: True for key in entitlements["features"]}
        entitlements["features"]["activity.log"] = False
        entitlements["limits"].update(
            {
                "projects.max": 2,
                "seats.purchased": 2,
                "repo_scopes.max_per_project": None,
                # Limits outside the contract may arrive as text.
                "custom.whole": "1024",
                "custom.fraction": "30.5",
                "custom.unlimited": "unlimited",
            }
        )
        entitlements["allow"] = {"access_surface_kinds": ["cli", "mcp"]}
        return free.model_copy(
            update={
                "plan_id": "team",
                "status": "active",
                "seat_quantity": 2,
                "entitlements": entitlements,
            }
        )


def team(mode: str, monkeypatch) -> EntitlementService:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", mode, raising=False)
    return EntitlementService(repository=_Team())


def denied(call) -> AppException:
    with pytest.raises(AppException) as caught:
        call()
    assert caught.value.status_code == 403
    assert caught.value.details["code"] == "entitlement_required"
    return caught.value


def test_required_mode_denies_what_the_plan_does_not_include(monkeypatch) -> None:
    service = team("required", monkeypatch)

    service.require_feature("org-1", "automation.hosted")
    error = denied(lambda: service.require_feature("org-1", "activity.log"))
    assert (error.details["plan_id"], error.details["feature"]) == ("team", "activity.log")

    service.require_capacity("org-1", "projects.max", 1)
    error = denied(lambda: service.require_capacity("org-1", "projects.max", 2))
    assert (error.details["reason"], error.details["maximum"]) == ("limit_exceeded", 2)

    service.require_allowed("org-1", "access_surface_kinds", "mcp")
    error = denied(lambda: service.require_allowed("org-1", "access_surface_kinds", "sandbox"))
    assert error.details["allowed"] == ["cli", "mcp"]


def test_shadow_mode_only_records_what_it_would_deny(monkeypatch, caplog) -> None:
    service = team("shadow", monkeypatch)
    with caplog.at_level("INFO"):
        service.require_feature("org-1", "activity.log")
        service.require_capacity("org-1", "projects.max", 5)
        service.require_allowed("org-1", "access_surface_kinds", "sandbox")
    would_deny = [r for r in caplog.records if r.getMessage() == "entitlement_shadow_would_deny"]
    assert len(would_deny) == 3
    assert service.enforced_limit_value("org-1", "projects.max") is None


def test_disabled_mode_decides_nothing_and_reads_nothing(monkeypatch) -> None:
    service = team("disabled", monkeypatch)
    service.require_feature("org-1", "activity.log")
    service.require_capacity("org-1", "projects.max", 99)
    service.require_allowed("org-1", "access_surface_kinds", "sandbox")
    assert service.enforced_limit_value("org-1", "projects.max") is None
    assert service._repo.reads == 0


@pytest.mark.parametrize("mode", ["shadow", "required"])
def test_a_missing_snapshot_blocks_only_in_required_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", mode, raising=False)
    service = EntitlementService(repository=_EmptyEntitlementRepository())
    calls = [
        lambda: service.require_capacity("org-1", "projects.max", 1),
        lambda: service.require_allowed("org-1", "access_surface_kinds", "cli"),
        lambda: service.enforced_limit_value("org-1", "projects.max"),
    ]
    for call in calls:
        if mode == "shadow":
            call()
        else:
            with pytest.raises(AppException) as caught:
                call()
            assert caught.value.status_code == 503


def test_limits_read_numbers_text_and_unlimited(monkeypatch) -> None:
    service = team("required", monkeypatch)
    assert service.limit_value("org-1", "projects.max") == 2
    assert service.limit_value("org-1", "custom.whole") == 1024
    assert service.limit_value("org-1", "custom.fraction") == 30.5
    assert service.limit_value("org-1", "custom.unlimited") is None
    assert service.limit_value("org-1", "repo_scopes.max_per_project") is None
    assert service.limit_value("org-1", "moons.max") is None
    service.require_capacity("org-1", "repo_scopes.max_per_project", 10_000)
    assert service.enforced_limit_value("org-1", "projects.max") == 2
    assert service.purchased_seats("org-1") == 2
    assert service.feature_enabled("org-1", "automation.hosted") is True
    assert service.feature_enabled("org-1", "activity.log") is False


def test_an_unreadable_limit_is_an_error_not_unlimited(monkeypatch) -> None:
    class _Garbled(_Team):
        def get_by_org_id(self, org_id: str):
            snapshot = super().get_by_org_id(org_id)
            snapshot.entitlements["limits"]["custom.whole"] = "lots"
            return snapshot

    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    monkeypatch.setattr(settings, "BILLING_ENFORCEMENT", "required", raising=False)
    service = EntitlementService(repository=_Garbled())
    with pytest.raises(AppException):
        service.limit_value("org-1", "custom.whole")


def test_entitlements_off_means_everything_open(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "disabled", raising=False)
    service = EntitlementService(repository=_EmptyEntitlementRepository())
    assert service.general_enforcement_mode == "disabled"
    assert service.purchased_seats("org-1") == 0
    assert service.feature_enabled("org-1", "activity.log") is True
    assert service.limit_value("org-1", "projects.max") is None
    assert service.get_snapshot("org-1").plan_id == "disabled"


def test_one_request_reads_the_snapshot_once(monkeypatch) -> None:
    service = team("required", monkeypatch)
    token = entitlement_snapshot_cache_var.set({})
    try:
        service.require_feature("org-1", "automation.hosted")
        service.require_capacity("org-1", "projects.max", 1)
        service.limit_value("org-1", "projects.max")
    finally:
        entitlement_snapshot_cache_var.reset(token)
    assert service._repo.reads == 1


def test_a_snapshot_without_a_time_zone_is_read_as_utc(monkeypatch) -> None:
    class _Naive(_Team):
        def get_by_org_id(self, org_id: str):
            return (
                super()
                .get_by_org_id(org_id)
                .model_copy(update={"effective_until": datetime(2999, 1, 1)})
            )

    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    snapshot = EntitlementService(repository=_Naive()).get_snapshot("org-1")
    assert snapshot.plan_id == "team"


def test_a_snapshot_with_an_unreadable_schema_version_is_refused(monkeypatch) -> None:
    class _Unreadable(_Team):
        def get_by_org_id(self, org_id: str):
            return super().get_by_org_id(org_id).model_copy(update={"schema_version": "one"})

    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_Unreadable()).get_snapshot("org-1")
    assert caught.value.details["code"] == "entitlement_snapshot_invalid"


def local_file(tmp_path, monkeypatch, payload) -> EntitlementService:
    path = tmp_path / "entitlements.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "local", raising=False)
    monkeypatch.setattr(settings, "LOCAL_ENTITLEMENTS_FILE", str(path), raising=False)
    service_module._load_local_entitlements.cache_clear()
    return EntitlementService(repository=_EmptyEntitlementRepository())


@pytest.fixture(autouse=True)
def _fresh_local_file_cache():
    service_module._load_local_entitlements.cache_clear()
    yield
    service_module._load_local_entitlements.cache_clear()


def test_a_self_hosted_file_merges_organization_overrides_over_the_default(
    tmp_path, monkeypatch
) -> None:
    base = _ValidEntitlementRepository().get_by_org_id("org-1").entitlements
    service = local_file(
        tmp_path,
        monkeypatch,
        {
            "default": base,
            "orgs": {
                "org-1": {"limits": {"projects.max": 9}, "features": {"automation.hosted": True}}
            },
        },
    )
    snapshot = service.get_snapshot("org-1")
    assert snapshot.source == "local"
    assert snapshot.entitlements["limits"]["projects.max"] == 9
    assert snapshot.entitlements["limits"]["repo_scopes.max_per_project"] == 2
    assert service.general_enforcement_mode == "required"
    assert service.get_snapshot("org-2").entitlements["limits"]["projects.max"] == 1


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (["not", "an", "object"], None),
        ({"orgs": {}}, "local_entitlement_file_invalid"),
        ({"default": {"limits": {"seats.purchased": "two"}}}, "local_entitlement_snapshot_invalid"),
    ],
)
def test_a_broken_self_hosted_file_fails_closed(tmp_path, monkeypatch, payload, code) -> None:
    service = local_file(tmp_path, monkeypatch, payload)
    with pytest.raises(AppException) as caught:
        service.get_snapshot("org-1")
    if code:
        assert caught.value.details["code"] == code


def test_a_missing_self_hosted_file_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "local", raising=False)
    monkeypatch.setattr(
        settings, "LOCAL_ENTITLEMENTS_FILE", str(tmp_path / "gone.json"), raising=False
    )
    with pytest.raises(AppException):
        EntitlementService(repository=_EmptyEntitlementRepository()).get_snapshot("org-1")
    monkeypatch.setattr(settings, "LOCAL_ENTITLEMENTS_FILE", "", raising=False)
    service_module._load_local_entitlements.cache_clear()
    with pytest.raises(AppException) as caught:
        EntitlementService(repository=_EmptyEntitlementRepository()).get_snapshot("org-1")
    assert caught.value.details["code"] == "local_entitlement_file_missing"


def test_unlimited_values_are_spelled_many_ways() -> None:
    for value in (None, "", "unlimited", "Infinite", " none ", "-1"):
        assert service_module._is_unlimited(value)
    for value in (0, 5, "5"):
        assert not service_module._is_unlimited(value)
