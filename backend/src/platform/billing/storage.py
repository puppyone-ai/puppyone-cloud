from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.infra.execution.runner import run_blocking
from src.infra.supabase.client import SupabaseClient
from src.version_engine.write_engine.tree import tree_to_flat

STORAGE_METRIC = "storage.logical_bytes"
logger = logging.getLogger(__name__)


class StorageUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    org_id: str
    metric: str = STORAGE_METRIC
    value: int = 0
    version: int = 0
    threshold_percent: int = 0
    full_reconciled_at: datetime | None = None


class StorageUsageRepository:
    TABLE = "organization_usage_counters"

    def __init__(self, supabase_client: SupabaseClient | None = None) -> None:
        self._client = (supabase_client or SupabaseClient()).get_client()

    def get(self, org_id: str) -> StorageUsage:
        response = (
            self._client.table(self.TABLE)
            .select("*")
            .eq("org_id", org_id)
            .eq("metric", STORAGE_METRIC)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return StorageUsage.model_validate(rows[0]) if rows else StorageUsage(org_id=org_id)

    def reconcile(
        self,
        *,
        org_id: str,
        value: int,
        limit: int | None,
        idempotency_key: str,
        source: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._client.rpc(
            "reconcile_organization_usage_counter",
            {
                "p_org_id": org_id,
                "p_metric": STORAGE_METRIC,
                "p_value": value,
                "p_limit": limit,
                "p_idempotency_key": idempotency_key,
                "p_source": source,
                "p_metadata": metadata,
            },
        ).execute()
        data = response.data
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("storage usage reconciliation returned an invalid response")
        return data

    def claim_reconciliation_batch(
        self,
        *,
        limit: int,
        min_age_seconds: int,
    ) -> list[str]:
        response = self._client.rpc(
            "claim_storage_reconciliation_batch",
            {
                "p_limit": limit,
                "p_min_age_seconds": min_age_seconds,
            },
        ).execute()
        rows = response.data or []
        if isinstance(rows, dict):
            rows = [rows]
        return [str(row["org_id"]) for row in rows if isinstance(row, dict) and row.get("org_id")]


def logical_tree_bytes(store: Any, root_hash: str) -> int:
    if not root_hash:
        return 0
    manifest = tree_to_flat(store, root_hash, include_gitlinks=False)
    # Gitlinks name commits in external repositories, not locally stored files.
    # Logical active size counts each path, even when content-addressed blobs
    # deduplicate physically. This is predictable to customers and providers.
    return sum(len(store.get(oid)) for oid in manifest.values())


def logical_tree_delta(store: Any, old_root_hash: str, new_root_hash: str) -> int:
    """Measure only changed logical paths instead of rereading every blob."""

    if old_root_hash == new_root_hash:
        return 0
    old_manifest = (
        tree_to_flat(store, old_root_hash, include_gitlinks=False) if old_root_hash else {}
    )
    new_manifest = (
        tree_to_flat(store, new_root_hash, include_gitlinks=False) if new_root_hash else {}
    )
    sizes: dict[str, int] = {}

    def size(oid: str) -> int:
        cached = sizes.get(oid)
        if cached is None:
            cached = len(store.get(oid))
            sizes[oid] = cached
        return cached

    delta = 0
    for path in old_manifest.keys() | new_manifest.keys():
        old_oid = old_manifest.get(path)
        new_oid = new_manifest.get(path)
        if old_oid == new_oid:
            continue
        if old_oid is not None:
            delta -= size(old_oid)
        if new_oid is not None:
            delta += size(new_oid)
    return delta


def oversized_new_logical_file(
    store: Any,
    old_root_hash: str,
    new_root_hash: str,
    limit_bytes: int,
) -> tuple[str, int] | None:
    """Find a newly introduced logical file above the plan limit.

    OID occurrence counts make a pure rename of a grandfathered oversized
    file legal, while copying that same blob to an additional path is treated
    as a new logical file and remains subject to the current plan.
    """

    old_manifest = (
        tree_to_flat(store, old_root_hash, include_gitlinks=False) if old_root_hash else {}
    )
    new_manifest = (
        tree_to_flat(store, new_root_hash, include_gitlinks=False) if new_root_hash else {}
    )
    excess = Counter(new_manifest.values()) - Counter(old_manifest.values())
    sizes: dict[str, int] = {}
    for path, oid in sorted(new_manifest.items()):
        if old_manifest.get(path) == oid:
            continue
        if excess[oid] <= 0:
            continue
        excess[oid] -= 1
        size = sizes.get(oid)
        if size is None:
            size = len(store.get(oid))
            sizes[oid] = size
        if size > limit_bytes:
            return path, size
    return None


class StorageReconciliationService:
    """Scheduled logical billing reconciliation over admitted native refs."""

    def __init__(self, *, repo_manager, usage_repository=None, checked_reconciler=None, **_):
        self._checked = checked_reconciler or repo_manager.create_usage_reconciler()
        self._usage = usage_repository or StorageUsageRepository()

    async def reconcile_once(self, *, limit, min_age_seconds):
        for _ in range(10):
            if await run_blocking(self._checked.prune) < 200:
                break
        org_ids = await run_blocking(
            self._usage.claim_reconciliation_batch, limit=limit, min_age_seconds=min_age_seconds
        )
        summary = {"claimed": len(org_ids), "reconciled": 0, "failed": 0}
        for org_id in org_ids:
            try:
                await run_blocking(self._checked.reconcile, org_id)
                summary["reconciled"] += 1
            except Exception:
                summary["failed"] += 1
                logger.exception("storage_reconciliation_failed", extra={"org_id": org_id})
        return summary
