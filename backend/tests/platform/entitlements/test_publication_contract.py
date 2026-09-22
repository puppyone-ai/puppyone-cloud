from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from src.exceptions import AppException
from src.platform.entitlements.models import EntitlementPublicationAck, EntitlementUpsert
from src.platform.entitlements.service import EntitlementService


class _Repository:
    def __init__(self) -> None:
        self.published: EntitlementUpsert | None = None

    def publish(self, payload: EntitlementUpsert):
        self.published = payload
        return EntitlementPublicationAck(
            outcome="inserted",
            source_revision=payload.source_revision,
            payload_hash=payload.payload_hash,
            snapshot=payload.model_dump(exclude={"source_event_id", "event_type"}),
        )


def _payload() -> dict:
    unsigned = {
        "org_id": "org-1",
        "schema_version": "1.1",
        "plan_id": "plus",
        "status": "active",
        "source": "puppypay",
        "entitlements": {
            "features": {
                "access_surface.agent": True,
                "access_surface.mcp": True,
                "access_surface.sandbox": True,
                "automation.hosted": True,
                "remote_workspace.create": True,
                "scope_sandbox.connect": True,
            },
            "limits": {
                "projects.max": None,
                "repo_scopes.max_per_project": 10,
                "storage.max_bytes": 20 * 1024**3,
                "upload.max_single_file_bytes": 200 * 1024**2,
                "seats.purchased": 2,
                "runtime.included_units": 200,
            },
            "allow": {"access_surface_kinds": ["direct"]},
        },
        "seat_quantity": 2,
        "catalog_version": "launch-v1",
        "source_revision": 8,
        "effective_at": datetime(2026, 7, 14, tzinfo=UTC),
        "current_period_end": None,
        "effective_until": None,
        "source_event_id": "event-8",
        "source_quote_id": "quote-8",
        "event_type": "subscription.updated",
    }
    canonical = json.dumps(
        EntitlementUpsert.model_construct(**unsigned, payload_hash="0" * 64).model_dump(
            mode="json", exclude={"payload_hash"}
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {**unsigned, "payload_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def test_publication_accepts_exact_canonical_hash_and_returns_ack() -> None:
    repository = _Repository()
    payload = EntitlementUpsert.model_validate(_payload())

    ack = EntitlementService(repository=repository).publish(payload)

    assert repository.published == payload
    assert ack.source_revision == 8
    assert ack.payload_hash == payload.payload_hash
    assert ack.snapshot.source_quote_id == "quote-8"


def test_publication_rejects_tampered_snapshot_before_database_write() -> None:
    repository = _Repository()
    raw = _payload()
    raw["status"] = "past_due"

    with pytest.raises(AppException) as caught:
        EntitlementService(repository=repository).publish(EntitlementUpsert.model_validate(raw))

    assert caught.value.status_code == 422
    assert caught.value.details["code"] == "entitlement_payload_hash_mismatch"
    assert repository.published is None


def test_publication_preserves_version_1_0_canonical_hash_during_rolling_deploy() -> None:
    repository = _Repository()
    unsigned = _payload()
    unsigned.pop("payload_hash")
    unsigned.pop("source_quote_id")
    unsigned["schema_version"] = "1.0"
    canonical_unsigned = EntitlementUpsert.model_construct(
        **unsigned,
        payload_hash="0" * 64,
    ).model_dump(mode="json", exclude={"payload_hash"})
    canonical_unsigned.pop("source_quote_id", None)
    canonical = json.dumps(
        canonical_unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload = EntitlementUpsert.model_validate(
        {**unsigned, "payload_hash": hashlib.sha256(canonical.encode()).hexdigest()}
    )

    ack = EntitlementService(repository=repository).publish(payload)

    assert ack.source_revision == 8
    assert repository.published == payload


def test_quote_correlation_requires_schema_version_1_1() -> None:
    raw = _payload()
    raw["schema_version"] = "1.0"

    with pytest.raises(ValueError, match="source_quote_id requires"):
        EntitlementUpsert.model_validate(raw)


def test_unknown_contract_major_and_missing_groups_are_rejected() -> None:
    raw = _payload()
    raw["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="unsupported entitlement schema major"):
        EntitlementUpsert.model_validate(raw)

    raw = _payload()
    raw["entitlements"].pop("allow")
    with pytest.raises(ValueError, match=r"entitlements[.]allow"):
        EntitlementUpsert.model_validate(raw)

    raw = _payload()
    raw["catalog_version"] = "legacy"
    with pytest.raises(ValueError, match="non-legacy catalog_version"):
        EntitlementUpsert.model_validate(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["entitlements"]["limits"].pop("storage.max_bytes"),
            "missing required keys",
        ),
        (
            lambda raw: raw["entitlements"]["features"].__setitem__("automation.hosted", "yes"),
            "must be a boolean",
        ),
        (
            lambda raw: raw["entitlements"]["limits"].__setitem__("seats.purchased", 3),
            "must equal seat_quantity",
        ),
        (
            lambda raw: raw["entitlements"]["limits"].__setitem__("runtime.included_units", None),
            "non-negative integer",
        ),
    ],
)
def test_mandatory_entitlement_values_fail_closed(mutate, message: str) -> None:
    raw = _payload()
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        EntitlementUpsert.model_validate(raw)
