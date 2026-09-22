from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from src.config import settings
from src.platform.billing.gateway import PuppyPayGateway
from src.platform.billing.operations import BillingOperation, BillingOperationRepository
from src.platform.organization.repository import OrganizationRepository
from src.utils.logger import log_error, log_info


class SeatProposalService:
    """Recoverable PuppyOne seat-operation outbox.

    The worker creates a commercial Quote only. It never changes the provider
    subscription and never grants product access; an organization owner must
    still accept the Quote through the BFF before PuppyPay changes seats.
    """

    def __init__(
        self,
        *,
        gateway: PuppyPayGateway | None = None,
        operation_repository: BillingOperationRepository | None = None,
        organization_repository: OrganizationRepository | None = None,
    ) -> None:
        self._gateway = gateway or PuppyPayGateway()
        self._operations = operation_repository
        self._organizations = organization_repository or OrganizationRepository()

    @property
    def _operation_repo(self) -> BillingOperationRepository:
        if self._operations is None:
            self._operations = BillingOperationRepository()
        return self._operations

    async def recover_once(self, *, limit: int = 25) -> dict[str, int]:
        if settings.SEAT_BILLING_MODE == "disabled":
            return {"claimed": 0, "quoted": 0, "failed": 0}
        claimed = await asyncio.to_thread(
            self._operation_repo.claim_seat_proposals,
            limit=limit,
            lease_seconds=settings.SEAT_PROPOSAL_LEASE_SECONDS,
        )
        quoted = 0
        for operation in claimed:
            if await self._process(operation):
                quoted += 1
        return {
            "claimed": len(claimed),
            "quoted": quoted,
            "failed": len(claimed) - quoted,
        }

    async def _process(self, operation: BillingOperation) -> bool:
        try:
            target = operation.target_seat_quantity
            if target is None or target <= 0:
                raise ValueError("seat proposal target must be positive")
            owner_user_id = await asyncio.to_thread(
                self._organizations.get_owner_user_id,
                operation.org_id,
            )
            if not owner_user_id:
                raise ValueError("organization owner is missing")

            quote = await self._gateway.request(
                "POST",
                "/internal/v1/billing/seat-proposals",
                idempotency_key=f"seat-proposal:{operation.id}",
                body={
                    "org_id": operation.org_id,
                    "proposed_seat_quantity": target,
                    "requested_by_user_id": owner_user_id,
                    "membership_event_id": operation.id,
                },
            )
            quote_id = str(quote.get("quote_id") or "")
            if not quote_id:
                raise ValueError("seat proposal response is missing quote_id")
            updated = await asyncio.to_thread(
                self._operation_repo.update_claimed_seat_proposal,
                operation,
                {
                    "status": "quoted",
                    "quote_id": quote_id,
                    "response_payload": {
                        **operation.response_payload,
                        "quote": quote,
                    },
                    "last_error": None,
                },
            )
            if updated is not None:
                log_info(f"Seat proposal quoted org={operation.org_id} operation={operation.id}")
            return True
        except Exception as exc:
            delay_seconds = min(3600, 2 ** min(max(operation.attempts, 1), 11))
            retry_status = (
                "awaiting_confirmation" if operation.kind == "member_activation" else "pending"
            )
            try:
                await asyncio.to_thread(
                    self._operation_repo.update_claimed_seat_proposal,
                    operation,
                    {
                        "status": retry_status,
                        "next_attempt_at": (
                            datetime.now(UTC) + timedelta(seconds=delay_seconds)
                        ).isoformat(),
                        "last_error": type(exc).__name__,
                    },
                )
            except Exception as update_exc:
                log_error(
                    "Seat proposal recovery update failed "
                    f"org={operation.org_id} error_type={type(update_exc).__name__}"
                )
            log_error(
                f"Seat proposal failed org={operation.org_id} error_type={type(exc).__name__}"
            )
            return False


_service: SeatProposalService | None = None


def get_seat_proposal_service() -> SeatProposalService:
    global _service
    if _service is None:
        _service = SeatProposalService()
    return _service
