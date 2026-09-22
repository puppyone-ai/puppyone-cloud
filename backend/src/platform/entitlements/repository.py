from __future__ import annotations

from datetime import datetime
from typing import Any

from src.infra.supabase.client import SupabaseClient
from src.platform.entitlements.models import (
    EntitlementPublicationAck,
    EntitlementSnapshot,
    EntitlementUpsert,
)


def _serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _rpc_object(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("entitlement publication RPC returned an invalid response")
    return data


class EntitlementRepository:
    TABLE = "organization_entitlements"

    def __init__(self, supabase_client: SupabaseClient | None = None):
        self._client = (supabase_client or SupabaseClient()).get_client()

    def get_by_org_id(self, org_id: str) -> EntitlementSnapshot | None:
        resp = self._client.table(self.TABLE).select("*").eq("org_id", org_id).limit(1).execute()
        rows = resp.data or []
        return EntitlementSnapshot(**rows[0]) if rows else None

    def publish(self, payload: EntitlementUpsert) -> EntitlementPublicationAck:
        """Atomically publish a monotonic snapshot and its audit event."""

        params = {
            "p_org_id": payload.org_id,
            "p_schema_version": payload.schema_version,
            "p_plan_id": payload.plan_id,
            "p_status": payload.status,
            "p_source": payload.source,
            "p_entitlements": payload.entitlements,
            "p_seat_quantity": payload.seat_quantity,
            "p_catalog_version": payload.catalog_version,
            "p_source_revision": payload.source_revision,
            "p_effective_at": _serialize_dt(payload.effective_at),
            "p_effective_until": _serialize_dt(payload.effective_until),
            "p_current_period_end": _serialize_dt(payload.current_period_end),
            "p_payload_hash": payload.payload_hash,
            "p_source_event_id": payload.source_event_id,
            "p_source_quote_id": payload.source_quote_id,
            "p_event_type": payload.event_type or "entitlement.published",
        }
        response = self._client.rpc("publish_organization_entitlement_v2", params).execute()
        return EntitlementPublicationAck.model_validate(_rpc_object(response.data))
