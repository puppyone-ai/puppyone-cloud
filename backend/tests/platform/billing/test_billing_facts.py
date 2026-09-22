from types import SimpleNamespace

from src.platform.billing.facts import BillingFactsService


class _Organizations:
    def count_billable_members(self, org_id: str) -> int:
        assert org_id == "org-1"
        return 4


class _Entitlements:
    def get_by_org_id(self, org_id: str):
        assert org_id == "org-1"
        return SimpleNamespace(
            seat_quantity=5,
            source_revision=17,
            payload_hash="a" * 64,
        )


class _Storage:
    def get(self, org_id: str):
        assert org_id == "org-1"
        return SimpleNamespace(value=1234, threshold_percent=95)


class _Operations:
    def count_pending(self, org_id: str) -> int:
        assert org_id == "org-1"
        return 2


class _Runtime:
    def count_orphans(self, org_id: str, *, now) -> int:
        assert org_id == "org-1"
        assert now.tzinfo is not None
        return 1


def test_billing_facts_exposes_reconciliation_values_without_financial_data():
    facts = BillingFactsService(
        organizations=_Organizations(),
        entitlements=_Entitlements(),
        storage=_Storage(),
        operations=_Operations(),
        runtime=_Runtime(),
    ).get("org-1")

    assert facts.model_dump(exclude={"observed_at"}) == {
        "schema_version": "1.0",
        "org_id": "org-1",
        "billable_seat_quantity": 4,
        "entitlement_seat_quantity": 5,
        "entitlement_source_revision": 17,
        "entitlement_payload_hash": "a" * 64,
        "runtime_orphan_count": 1,
        "pending_billing_operation_count": 2,
        "storage_logical_bytes": 1234,
        "storage_threshold_percent": 95,
    }
