from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from src.config import settings
from src.platform.billing.gateway import PuppyPayGateway
from src.platform.billing.operations import BillingOperation, BillingOperationRepository
from src.platform.entitlements.models import EntitlementUpsert
from src.platform.entitlements.service import EntitlementService
from src.utils.logger import log_error, log_info


class EntitlementProvisioningService:
    """Recoverable PuppyPay-to-PuppyOne initialization for organization snapshots."""

    def __init__(
        self,
        *,
        gateway: PuppyPayGateway | None = None,
        operation_repository: BillingOperationRepository | None = None,
        entitlement_service: EntitlementService | None = None,
    ) -> None:
        self._gateway = gateway or PuppyPayGateway()
        self._operations = operation_repository
        self._entitlements = entitlement_service or EntitlementService()

    @property
    def _operation_repo(self) -> BillingOperationRepository:
        if self._operations is None:
            self._operations = BillingOperationRepository()
        return self._operations

    def enqueue(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
    ) -> BillingOperation | None:
        if settings.ENTITLEMENTS_MODE != "db":
            return None
        return self._operation_repo.enqueue_entitlement_provisioning(
            org_id=org_id,
            actor_user_id=actor_user_id,
        )

    async def ensure(
        self,
        *,
        org_id: str,
        actor_user_id: str | None,
    ) -> bool:
        operation = await asyncio.to_thread(
            self.enqueue,
            org_id=org_id,
            actor_user_id=actor_user_id,
        )
        if operation is None:
            return True
        if operation.status == "confirmed":
            return True
        return await self._process(operation)

    async def recover_once(self, *, limit: int = 25) -> dict[str, int]:
        if settings.ENTITLEMENTS_MODE != "db":
            return {"enqueued": 0, "claimed": 0, "succeeded": 0, "failed": 0}
        enqueued = await asyncio.to_thread(
            self._operation_repo.enqueue_missing_entitlement_provisioning,
            limit=max(100, limit),
        )
        claimed = await asyncio.to_thread(
            self._operation_repo.claim_entitlement_provisioning,
            limit=limit,
            lease_seconds=settings.ENTITLEMENT_PROVISIONING_LEASE_SECONDS,
        )
        succeeded = 0
        for operation in claimed:
            if await self._process(operation):
                succeeded += 1
        return {
            "enqueued": enqueued,
            "claimed": len(claimed),
            "succeeded": succeeded,
            "failed": len(claimed) - succeeded,
        }

    async def _process(self, operation: BillingOperation) -> bool:
        try:
            payload = await self._gateway.request(
                "POST",
                "/internal/v1/billing/organizations/provision",
                actor_user_id=operation.actor_user_id,
                idempotency_key=operation.idempotency_key,
                body={
                    "org_id": operation.org_id,
                    "billing_manager_user_id": operation.actor_user_id,
                },
            )
            snapshot = EntitlementUpsert.model_validate(payload)
            if snapshot.org_id != operation.org_id:
                raise ValueError("provisioned entitlement organization mismatch")
            acknowledgement = await asyncio.to_thread(self._entitlements.publish, snapshot)
            await asyncio.to_thread(
                self._operation_repo.update,
                operation.id,
                {
                    "status": "confirmed",
                    "confirmed_revision": acknowledgement.source_revision,
                    "response_payload": {
                        "schema_version": "1.0",
                        "source_revision": acknowledgement.source_revision,
                        "payload_hash": acknowledgement.payload_hash,
                    },
                    "completed_at": datetime.now(UTC).isoformat(),
                    "last_error": None,
                },
            )
            log_info(
                "Entitlement provisioning completed "
                f"org={operation.org_id} revision={acknowledgement.source_revision}"
            )
            return True
        except Exception as exc:
            # The operation row and lease make every failure retryable. Store
            # only the exception type so provider bodies and secrets cannot
            # leak into product-side audit rows.
            delay_seconds = min(3600, 2 ** min(max(operation.attempts, 1), 11))
            try:
                await asyncio.to_thread(
                    self._operation_repo.update,
                    operation.id,
                    {
                        "status": "failed",
                        "next_attempt_at": (
                            datetime.now(UTC) + timedelta(seconds=delay_seconds)
                        ).isoformat(),
                        "last_error": type(exc).__name__,
                    },
                )
            except Exception as update_exc:
                log_error(
                    "Entitlement provisioning recovery update failed "
                    f"org={operation.org_id} error_type={type(update_exc).__name__}"
                )
            log_error(
                "Entitlement provisioning failed "
                f"org={operation.org_id} error_type={type(exc).__name__}"
            )
            return False


_service: EntitlementProvisioningService | None = None


def get_entitlement_provisioning_service() -> EntitlementProvisioningService:
    global _service
    if _service is None:
        _service = EntitlementProvisioningService()
    return _service
