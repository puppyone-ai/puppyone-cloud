from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from src.config import settings
from src.infra.supabase.client import SupabaseClient
from src.platform.billing.operations import BillingOperationRepository
from src.platform.billing.storage import StorageUsageRepository
from src.platform.entitlements.repository import EntitlementRepository
from src.platform.organization.repository import OrganizationRepository


class BillingOrganizationFacts(BaseModel):
    """Non-financial product facts used by cross-service reconciliation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    org_id: str
    billable_seat_quantity: int
    entitlement_seat_quantity: int
    entitlement_source_revision: int
    entitlement_payload_hash: str | None
    runtime_orphan_count: int
    pending_billing_operation_count: int
    storage_logical_bytes: int
    storage_threshold_percent: int
    observed_at: datetime


class RuntimeBillingFactsRepository:
    TABLE = "runtime_billing_runs"

    def __init__(self, supabase_client: SupabaseClient | None = None) -> None:
        self._client = (supabase_client or SupabaseClient()).get_client()

    def count_orphans(self, org_id: str, *, now: datetime) -> int:
        failed = (
            self._client.table(self.TABLE)
            .select("run_id", count="exact")
            .eq("org_id", org_id)
            .in_("status", ["settling", "failed", "reservation_failed"])
            .execute()
        )
        expired = (
            self._client.table(self.TABLE)
            .select("run_id", count="exact")
            .eq("org_id", org_id)
            .in_("status", ["reserved", "running"])
            .lte("expires_at", now.isoformat())
            .execute()
        )
        stale_pending = (
            self._client.table(self.TABLE)
            .select("run_id", count="exact")
            .eq("org_id", org_id)
            .eq("status", "pending_reservation")
            .lte(
                "updated_at",
                (now - timedelta(seconds=settings.RUNTIME_RESERVATION_CLAIM_SECONDS)).isoformat(),
            )
            .execute()
        )
        return int(failed.count or 0) + int(expired.count or 0) + int(stale_pending.count or 0)


class BillingFactsService:
    def __init__(
        self,
        *,
        organizations: OrganizationRepository | None = None,
        entitlements: EntitlementRepository | None = None,
        storage: StorageUsageRepository | None = None,
        operations: BillingOperationRepository | None = None,
        runtime: RuntimeBillingFactsRepository | None = None,
    ) -> None:
        self._organizations = organizations or OrganizationRepository()
        self._entitlements = entitlements or EntitlementRepository()
        self._storage = storage or StorageUsageRepository()
        self._operations = operations or BillingOperationRepository()
        self._runtime = runtime or RuntimeBillingFactsRepository()

    def get(self, org_id: str) -> BillingOrganizationFacts:
        observed_at = datetime.now(UTC)
        entitlement = self._entitlements.get_by_org_id(org_id)
        usage = self._storage.get(org_id)
        return BillingOrganizationFacts(
            org_id=org_id,
            billable_seat_quantity=self._organizations.count_billable_members(org_id),
            entitlement_seat_quantity=entitlement.seat_quantity if entitlement else 0,
            entitlement_source_revision=entitlement.source_revision if entitlement else 0,
            entitlement_payload_hash=entitlement.payload_hash if entitlement else None,
            runtime_orphan_count=self._runtime.count_orphans(org_id, now=observed_at),
            pending_billing_operation_count=self._operations.count_pending(org_id),
            storage_logical_bytes=usage.value,
            storage_threshold_percent=usage.threshold_percent,
            observed_at=observed_at,
        )
