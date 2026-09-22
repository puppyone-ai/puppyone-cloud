from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.entitlements.models import (
    EntitlementPublicationAck,
    EntitlementSnapshot,
    EntitlementUpsert,
    validate_entitlement_values,
)
from src.platform.entitlements.repository import EntitlementRepository

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_unlimited(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unlimited", "infinite", "none", "-1"}
    return isinstance(value, (int, float)) and value < 0


@lru_cache(maxsize=1)
def _load_local_entitlements() -> dict[str, Any] | None:
    raw_path = (settings.LOCAL_ENTITLEMENTS_FILE or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            message=f"LOCAL_ENTITLEMENTS_FILE does not exist: {path}",
        )
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            message="LOCAL_ENTITLEMENTS_FILE must contain a JSON object",
        )
    return loaded


class EntitlementService:
    def __init__(self, repository: EntitlementRepository | None = None):
        self._repository = repository

    @property
    def enabled(self) -> bool:
        return settings.ENTITLEMENTS_MODE != "disabled"

    @property
    def general_enforcement_mode(self) -> str:
        if settings.ENTITLEMENTS_MODE == "local":
            return "required"
        if settings.ENTITLEMENTS_MODE == "db":
            return settings.BILLING_ENFORCEMENT
        return "disabled"

    @property
    def _repo(self) -> EntitlementRepository:
        if self._repository is None:
            self._repository = EntitlementRepository()
        return self._repository

    def get_snapshot(self, org_id: str) -> EntitlementSnapshot:
        # Per-request memo (set by RequestContextMiddleware): collapse the
        # repeated snapshot fetches that the entitlement gates do within one
        # request (each require_*/limit_value/feature_enabled calls this) down
        # to a single fetch. Falls through to a direct fetch off-request
        # (workers/scripts) where the contextvar is unset.
        from src.utils.request_context import entitlement_snapshot_cache_var

        cache = entitlement_snapshot_cache_var.get()
        if cache is not None and org_id in cache:
            return cache[org_id]
        snapshot = self._compute_snapshot(org_id)
        if cache is not None:
            cache[org_id] = snapshot
        return snapshot

    def _compute_snapshot(self, org_id: str) -> EntitlementSnapshot:
        if not self.enabled:
            return EntitlementSnapshot(
                org_id=org_id,
                plan_id="disabled",
                status="active",
                source="system",
                entitlements={"features": {}, "limits": {}, "allow": {}},
            )

        if settings.ENTITLEMENTS_MODE == "local":
            loaded = _load_local_entitlements()
            if loaded is None:
                raise AppException(
                    code=ErrorCode.INTERNAL_SERVER_ERROR,
                    status_code=503,
                    message="Local entitlement mode requires LOCAL_ENTITLEMENTS_FILE",
                    details={"code": "local_entitlement_file_missing"},
                )
            org_overrides = loaded.get("orgs", {}).get(org_id, {})
            default_entitlements = loaded.get("default")
            if not isinstance(default_entitlements, dict) or not isinstance(org_overrides, dict):
                raise AppException(
                    code=ErrorCode.INTERNAL_SERVER_ERROR,
                    status_code=503,
                    message="Local entitlement file must define object-valued default and org overrides",
                    details={"code": "local_entitlement_file_invalid"},
                )
            entitlements = _deep_merge(default_entitlements, org_overrides)
            seat_quantity = (entitlements.get("limits") or {}).get("seats.purchased")
            try:
                validate_entitlement_values(
                    entitlements,
                    seat_quantity=seat_quantity,
                )
            except (TypeError, ValueError) as exc:
                raise AppException(
                    code=ErrorCode.INTERNAL_SERVER_ERROR,
                    status_code=503,
                    message="Local entitlement snapshot is invalid",
                    details={"code": "local_entitlement_snapshot_invalid"},
                ) from exc
            return EntitlementSnapshot(
                org_id=org_id,
                plan_id=str(loaded.get("plan_id", "local")),
                status="active",
                source="local",
                seat_quantity=int(seat_quantity),
                catalog_version=str(loaded.get("catalog_version", "local")),
                entitlements=entitlements,
            )

        snapshot = self._repo.get_by_org_id(org_id)
        if snapshot is None:
            raise AppException(
                code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=503,
                message="Hosted entitlement snapshot is not available",
                details={
                    "code": "entitlement_snapshot_missing",
                    "org_id": org_id,
                    "retryable": True,
                },
            )
        if snapshot.effective_until is not None:
            expires_at = snapshot.effective_until
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                raise AppException(
                    code=ErrorCode.INTERNAL_SERVER_ERROR,
                    status_code=503,
                    message="Hosted entitlement snapshot has expired",
                    details={
                        "code": "entitlement_snapshot_expired",
                        "org_id": org_id,
                        "retryable": True,
                    },
                )
        # DB mode is the Hosted product projection. Every row must therefore
        # be an acknowledged PuppyPay publication; accepting a legacy/local
        # row here would silently restore the old unlimited fallback.
        try:
            schema_major = int(snapshot.schema_version.split(".", 1)[0])
        except (AttributeError, TypeError, ValueError):
            schema_major = -1
        if (
            snapshot.source != "puppypay"
            or snapshot.source_revision <= 0
            or not snapshot.payload_hash
            or schema_major != 1
            or snapshot.catalog_version in {"", "legacy"}
            or snapshot.effective_at is None
        ):
            raise AppException(
                code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=503,
                message="Hosted entitlement snapshot is invalid",
                details={"code": "entitlement_snapshot_invalid"},
            )
        try:
            validate_entitlement_values(
                snapshot.entitlements,
                seat_quantity=snapshot.seat_quantity,
            )
        except (TypeError, ValueError) as exc:
            raise AppException(
                code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=503,
                message="Hosted entitlement snapshot is invalid",
                details={"code": "entitlement_snapshot_invalid"},
            ) from exc
        return snapshot

    def publish(self, payload: EntitlementUpsert) -> EntitlementPublicationAck:
        unsigned = payload.model_dump(mode="json", exclude={"payload_hash"})
        # Version 1.0 predates quote correlation. Pydantic materializes the new
        # optional field as ``None`` when it reads a stored 1.0 outbox item, but
        # that field was not present in the producer's signed bytes. Preserve
        # the historical canonical form during a rolling deployment.
        if payload.schema_version == "1.0":
            unsigned.pop("source_quote_id", None)
        canonical = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if computed_hash != payload.payload_hash:
            raise AppException(
                code=ErrorCode.VALIDATION_ERROR,
                status_code=422,
                message="Entitlement payload hash mismatch",
                details={"code": "entitlement_payload_hash_mismatch"},
            )
        return self._repo.publish(payload)

    def purchased_seats(self, org_id: str) -> int:
        if not self.enabled:
            return 0
        return max(0, int(self.get_snapshot(org_id).seat_quantity))

    def feature_enabled(self, org_id: str, feature_key: str) -> bool:
        if not self.enabled:
            return True
        snapshot = self.get_snapshot(org_id)
        value = (snapshot.entitlements.get("features") or {}).get(feature_key)
        return bool(value)

    def require_feature(self, org_id: str, feature_key: str) -> None:
        mode = self.general_enforcement_mode
        if mode == "disabled":
            return
        try:
            snapshot = self.get_snapshot(org_id)
        except AppException as exc:
            if mode == "shadow":
                self._log_shadow_unavailable(org_id, exc)
                return
            raise
        value = (snapshot.entitlements.get("features") or {}).get(feature_key)
        if bool(value):
            return
        if mode == "shadow":
            self._log_shadow_denial(
                org_id,
                "feature_not_enabled",
                feature=feature_key,
            )
            return
        self._raise_denied(
            org_id=org_id,
            plan_id=snapshot.plan_id,
            reason="feature_not_enabled",
            feature=feature_key,
        )

    def limit_value(self, org_id: str, limit_key: str) -> int | float | None:
        if not self.enabled:
            return None
        snapshot = self.get_snapshot(org_id)
        raw = (snapshot.entitlements.get("limits") or {}).get(limit_key)
        if _is_unlimited(raw):
            return None
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, (int, float)):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                try:
                    return float(raw)
                except ValueError as exc:
                    raise AppException(
                        code=ErrorCode.INTERNAL_SERVER_ERROR,
                        status_code=500,
                        message=f"Invalid entitlement limit value for {limit_key}: {raw}",
                    ) from exc
        return None

    def enforced_limit_value(self, org_id: str, limit_key: str) -> int | float | None:
        """Return a general product limit only when that rollout gate blocks.

        Storage has its own disabled/shadow/required mode and therefore reads
        the raw ``limit_value``. Upload and protocol admission use this method
        so turning on the DB projection does not silently enable enforcement
        before the global Billing gate reaches ``required``.
        """

        mode = self.general_enforcement_mode
        if mode == "disabled":
            return None
        try:
            value = self.limit_value(org_id, limit_key)
        except AppException as exc:
            if mode == "shadow":
                self._log_shadow_unavailable(org_id, exc)
                return None
            raise
        if mode == "shadow":
            return None
        return value

    def require_capacity(self, org_id: str, limit_key: str, current_count: int) -> None:
        mode = self.general_enforcement_mode
        if mode == "disabled":
            return
        try:
            maximum = self.limit_value(org_id, limit_key)
        except AppException as exc:
            if mode == "shadow":
                self._log_shadow_unavailable(org_id, exc)
                return
            raise
        if maximum is None or current_count < maximum:
            return
        snapshot = self.get_snapshot(org_id)
        if mode == "shadow":
            self._log_shadow_denial(
                org_id,
                "limit_exceeded",
                limit=limit_key,
                current=current_count,
                maximum=maximum,
            )
            return
        self._raise_denied(
            org_id=org_id,
            plan_id=snapshot.plan_id,
            reason="limit_exceeded",
            limit=limit_key,
            current=current_count,
            maximum=maximum,
        )

    def require_allowed(self, org_id: str, allow_key: str, requested_value: str) -> None:
        mode = self.general_enforcement_mode
        if mode == "disabled":
            return
        try:
            snapshot = self.get_snapshot(org_id)
        except AppException as exc:
            if mode == "shadow":
                self._log_shadow_unavailable(org_id, exc)
                return
            raise
        allowed = (snapshot.entitlements.get("allow") or {}).get(allow_key)
        if allowed == "*" or (isinstance(allowed, list) and requested_value in allowed):
            return
        if mode == "shadow":
            self._log_shadow_denial(
                org_id,
                "value_not_allowed",
                allow=allow_key,
                requested=requested_value,
            )
            return
        self._raise_denied(
            org_id=org_id,
            plan_id=snapshot.plan_id,
            reason="value_not_allowed",
            allow=allow_key,
            requested=requested_value,
            allowed=allowed or [],
        )

    def _raise_denied(
        self,
        *,
        org_id: str,
        plan_id: str,
        reason: str,
        **details: Any,
    ) -> None:
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=403,
            message="Entitlement required",
            details={
                "code": "entitlement_required",
                "org_id": org_id,
                "plan_id": plan_id,
                "reason": reason,
                **details,
            },
        )

    @staticmethod
    def _log_shadow_unavailable(org_id: str, error: AppException) -> None:
        details = error.details if isinstance(error.details, dict) else {}
        logger.warning(
            "entitlement_shadow_snapshot_unavailable",
            extra={
                "org_id": org_id,
                "reason": details.get("code", "entitlement_snapshot_unavailable"),
            },
        )

    @staticmethod
    def _log_shadow_denial(org_id: str, reason: str, **details: Any) -> None:
        logger.info(
            "entitlement_shadow_would_deny",
            extra={"org_id": org_id, "reason": reason, **details},
        )
