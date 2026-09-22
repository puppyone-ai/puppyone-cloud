"""Resumable, two-pass inventory for legacy Project-owned S3 namespaces."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from src.infra.supabase.client import SupabaseClient

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_LEGACY_PROJECT_KEY = re.compile(
    rf"^users/(?P<principal>{_SEGMENT})/"
    rf"(?:etl_artifacts|processed|raw)/(?P<project>{_SEGMENT})/"
)


def parse_legacy_project_storage_key(key: str) -> tuple[str, str] | None:
    """Return ``(project_id, principal)`` for an exact legacy owned key."""

    matched = _LEGACY_PROJECT_KEY.match(key)
    if matched is None:
        return None
    return matched.group("project"), matched.group("principal")


@dataclass(frozen=True, slots=True)
class InventoryProof:
    object_count: int
    multipart_count: int
    digest: str


class ProjectStorageInventoryRepository:
    def __init__(self, client=None) -> None:
        self._client = client or SupabaseClient().get_client()

    def record_batch(
        self,
        *,
        batch_key: str,
        principals: list[dict[str, str]],
        checkpoint: dict[str, Any],
        object_count: int,
        multipart_count: int,
    ) -> dict[str, Any]:
        response = self._client.rpc(
            "record_project_storage_inventory_batch",
            {
                "p_batch_key": batch_key,
                "p_principals": principals,
                "p_checkpoint": checkpoint,
                "p_observed_object_count": object_count,
                "p_observed_multipart_count": multipart_count,
            },
        ).execute()
        return _rpc_object(response.data)

    def checkpoint(self) -> dict[str, Any]:
        response = (
            self._client.table("project_storage_inventory_state")
            .select("checkpoint,inventory_complete")
            .eq("singleton", True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("Project storage inventory state is missing")
        if bool(rows[0].get("inventory_complete")):
            return {"inventory_complete": True}
        checkpoint = rows[0].get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise RuntimeError("Project storage inventory checkpoint is invalid")
        return checkpoint

    def finalize_scan(self, proof: InventoryProof) -> dict[str, Any]:
        response = self._client.rpc(
            "finalize_project_storage_inventory_scan",
            {
                "p_observed_object_count": proof.object_count,
                "p_observed_multipart_count": proof.multipart_count,
                "p_inventory_digest": proof.digest,
            },
        ).execute()
        return _rpc_object(response.data)

    def verify_scan(self, proof: InventoryProof) -> dict[str, Any]:
        response = self._client.rpc(
            "verify_project_storage_inventory",
            {
                "p_observed_object_count": proof.object_count,
                "p_observed_multipart_count": proof.multipart_count,
                "p_inventory_digest": proof.digest,
            },
        ).execute()
        return _rpc_object(response.data)

    def complete(self) -> dict[str, Any]:
        response = self._client.rpc(
            "complete_project_storage_inventory",
            {},
        ).execute()
        return _rpc_object(response.data)

    def live_project_ids(self) -> set[str]:
        project_ids: set[str] = set()
        offset = 0
        while True:
            response = (
                self._client.table("projects")
                .select("id")
                .range(offset, offset + 999)
                .execute()
            )
            rows = response.data or []
            project_ids.update(str(row["id"]) for row in rows if row.get("id"))
            if len(rows) < 1000:
                return project_ids
            offset += 1000

    def pending_orphans(self) -> list[tuple[str, str]]:
        response = (
            self._client.table("project_storage_orphan_prefixes")
            .select("project_id,principal")
            .eq("status", "pending")
            .execute()
        )
        return sorted(
            (str(row["project_id"]), str(row["principal"]))
            for row in (response.data or [])
        )

    def mark_orphan_cleaned(self, project_id: str, principal: str) -> None:
        response = self._client.rpc(
            "mark_project_storage_orphan_cleaned",
            {"p_project_id": project_id, "p_principal": principal},
        ).execute()
        if not bool(response.data):
            raise RuntimeError("Project storage orphan cleanup was not acknowledged")


def _rpc_object(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Project storage inventory RPC returned invalid data")
    return data


PageCallback = Callable[
    [str, list[tuple[str, str]], set[tuple[str, str]], dict[str, Any]], None
]


def observe_project_storage_inventory(
    client: Any,
    bucket: str,
    *,
    page_size: int = 1000,
    on_page: PageCallback | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> InventoryProof:
    """Observe every ``users/`` object and multipart upload exactly once."""

    digest = hashlib.sha256()
    object_count = 0
    multipart_count = 0

    checkpoint = checkpoint or {}
    phase = str(checkpoint.get("phase") or "objects")
    if phase not in {"objects", "multipart", "complete"}:
        raise RuntimeError("Project storage inventory checkpoint has invalid phase")
    objects_complete = phase in {"multipart", "complete"} or bool(
        checkpoint.get("objects_complete")
    )
    continuation_value = checkpoint.get("continuation_token")
    if continuation_value is not None and not isinstance(continuation_value, str):
        raise RuntimeError("Project storage object checkpoint is invalid")
    continuation: str | None = continuation_value
    while not objects_complete:
        params: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": "users/",
            "MaxKeys": page_size,
        }
        if continuation:
            params["ContinuationToken"] = continuation
        response = client.list_objects_v2(**params)
        keys = [str(item["Key"]) for item in response.get("Contents", [])]
        entries = [(key, "") for key in keys]
        principals = _principals(keys)
        for key in keys:
            digest.update(b"object\0")
            digest.update(key.encode("utf-8"))
            digest.update(b"\0")
        object_count += len(keys)
        next_token = response.get("NextContinuationToken")
        if response.get("IsTruncated") and not next_token:
            raise RuntimeError("S3 object inventory omitted its continuation token")
        if on_page is not None:
            on_page(
                "objects",
                entries,
                principals,
                {
                    "phase": "objects" if response.get("IsTruncated") else "multipart",
                    "objects_complete": not bool(response.get("IsTruncated")),
                    "continuation_token": next_token,
                },
            )
        if not response.get("IsTruncated"):
            objects_complete = True
            break
        continuation = str(next_token)

    key_value = checkpoint.get("key_marker") if phase == "multipart" else None
    upload_value = (
        checkpoint.get("upload_id_marker") if phase == "multipart" else None
    )
    if key_value is not None and not isinstance(key_value, str):
        raise RuntimeError("Project storage multipart checkpoint is invalid")
    if upload_value is not None and not isinstance(upload_value, str):
        raise RuntimeError("Project storage multipart checkpoint is invalid")
    key_marker: str | None = key_value
    upload_id_marker: str | None = upload_value
    multipart_complete = phase == "complete" or bool(
        checkpoint.get("multipart_complete")
    )
    while not multipart_complete:
        params = {
            "Bucket": bucket,
            "Prefix": "users/",
            "MaxUploads": page_size,
        }
        if key_marker is not None:
            params["KeyMarker"] = key_marker
            params["UploadIdMarker"] = upload_id_marker or ""
        response = client.list_multipart_uploads(**params)
        uploads = [
            (str(item["Key"]), str(item["UploadId"]))
            for item in response.get("Uploads", [])
        ]
        principals = _principals(key for key, _upload_id in uploads)
        for key, upload_id in uploads:
            digest.update(b"multipart\0")
            digest.update(key.encode("utf-8"))
            digest.update(b"\0")
            digest.update(upload_id.encode("utf-8"))
            digest.update(b"\0")
        multipart_count += len(uploads)
        next_key = response.get("NextKeyMarker")
        next_upload = response.get("NextUploadIdMarker")
        if response.get("IsTruncated") and (not next_key or not next_upload):
            raise RuntimeError(
                "S3 multipart inventory requires both KeyMarker and UploadIdMarker"
            )
        if on_page is not None:
            on_page(
                "multipart",
                uploads,
                principals,
                {
                    "phase": "multipart" if response.get("IsTruncated") else "complete",
                    "objects_complete": True,
                    "multipart_complete": not bool(response.get("IsTruncated")),
                    "key_marker": next_key,
                    "upload_id_marker": next_upload,
                },
            )
        if not response.get("IsTruncated"):
            multipart_complete = True
            break
        key_marker = str(next_key)
        upload_id_marker = str(next_upload)

    return InventoryProof(
        object_count=object_count,
        multipart_count=multipart_count,
        digest=digest.hexdigest(),
    )


def run_project_storage_inventory(
    client: Any,
    bucket: str,
    repository: ProjectStorageInventoryRepository,
    *,
    apply: bool,
) -> InventoryProof:
    """Record pass one, independently verify pass two, then open deletion."""

    discovered_principals: set[tuple[str, str]] = set()

    def record(
        kind: str,
        entries: list[tuple[str, str]],
        principals: set[tuple[str, str]],
        checkpoint: dict[str, Any],
    ) -> None:
        discovered_principals.update(principals)
        if not apply:
            return
        canonical = json.dumps(
            {"kind": kind, "entries": entries},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        outcome = repository.record_batch(
            batch_key=hashlib.sha256(canonical).hexdigest(),
            principals=[
                {"project_id": project_id, "principal": principal}
                for project_id, principal in sorted(principals)
            ],
            checkpoint={"kind": kind, **checkpoint},
            object_count=len(entries) if kind == "objects" else 0,
            multipart_count=len(entries) if kind == "multipart" else 0,
        )
        if outcome.get("outcome") not in {"recorded", "replayed"}:
            raise RuntimeError(f"Inventory batch was rejected: {outcome}")

    discovery_checkpoint = repository.checkpoint() if apply else None
    if discovery_checkpoint and discovery_checkpoint.get("inventory_complete"):
        raise RuntimeError("Project storage inventory is already complete")
    discovery = observe_project_storage_inventory(
        client,
        bucket,
        on_page=record,
        checkpoint=discovery_checkpoint,
    )
    live_project_ids = repository.live_project_ids()
    unknown = {
        (project_id, principal)
        for project_id, principal in discovered_principals
        if project_id not in live_project_ids
    }
    if unknown and not apply:
        sample = ", ".join(
            f"users/{principal}/.../{project_id}/" for project_id, principal in sorted(unknown)[:5]
        )
        raise RuntimeError(
            "Historical storage contains unknown Project prefixes; "
            f"run --apply for controlled cleanup: {sample}"
        )
    if apply:
        _cleanup_orphan_prefixes(client, bucket, repository)

    # Orphan cleanup can change the inventory. The authoritative pair of
    # proofs begins only after all unknown Project prefixes are absent.
    first = (
        observe_project_storage_inventory(client, bucket)
        if apply
        else discovery
    )
    if apply:
        finalized = repository.finalize_scan(first)
        if finalized.get("outcome") not in {"finalized", "already_complete"}:
            raise RuntimeError(f"Inventory finalization was rejected: {finalized}")

    # A fresh listing from the beginning is the independent start/end delta
    # proof. New writes after migration also update the relational principal
    # ledger, but any change during this historical scan still fails closed.
    second = observe_project_storage_inventory(client, bucket)
    if second != first:
        raise RuntimeError(
            "Project storage changed between inventory passes; rerun the scan"
        )
    if apply:
        verified = repository.verify_scan(second)
        if verified.get("outcome") != "verified":
            raise RuntimeError(f"Inventory verification was rejected: {verified}")
        completed = repository.complete()
        if completed.get("outcome") not in {"completed", "replayed"}:
            raise RuntimeError(f"Inventory completion was rejected: {completed}")
    return first


def _principals(keys: Iterable[str]) -> set[tuple[str, str]]:
    principals: set[tuple[str, str]] = set()
    for key in keys:
        parsed = parse_legacy_project_storage_key(key)
        if parsed is not None:
            principals.add(parsed)
    return principals


def _cleanup_orphan_prefixes(
    client: Any,
    bucket: str,
    repository: ProjectStorageInventoryRepository,
) -> None:
    for project_id, principal in repository.pending_orphans():
        if not re.fullmatch(_SEGMENT, project_id) or not re.fullmatch(
            _SEGMENT, principal
        ):
            raise RuntimeError("Inventory contains an invalid orphan prefix")
        prefixes = [
            f"users/{principal}/{namespace}/{project_id}/"
            for namespace in ("etl_artifacts", "processed", "raw")
        ]
        for prefix in prefixes:
            _purge_exact_prefix(client, bucket, prefix)
        for prefix in prefixes:
            objects = client.list_objects_v2(
                Bucket=bucket, Prefix=prefix, MaxKeys=1
            ).get("Contents", [])
            uploads = client.list_multipart_uploads(
                Bucket=bucket, Prefix=prefix, MaxUploads=1
            ).get("Uploads", [])
            if objects or uploads:
                raise RuntimeError(f"Orphan Project prefix is not empty: {prefix}")
        repository.mark_orphan_cleaned(project_id, principal)


def _purge_exact_prefix(client: Any, bucket: str, prefix: str) -> None:
    while True:
        response = client.list_objects_v2(
            Bucket=bucket, Prefix=prefix, MaxKeys=1000
        )
        keys = [str(item["Key"]) for item in response.get("Contents", [])]
        if not keys:
            break
        deleted = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )
        errors = deleted.get("Errors", [])
        if errors:
            sample = "; ".join(
                f"{item.get('Key', '(unknown)')}: "
                f"{item.get('Code', 'delete_failed')}"
                for item in errors[:3]
                if isinstance(item, dict)
            )
            raise RuntimeError(
                "S3 reported per-object failures during orphan cleanup"
                + (f": {sample}" if sample else "")
            )

    # Re-read the first page after every abort batch. Pagination markers refer
    # to a changing set once the current page is removed and can otherwise
    # skip uploads that moved into the preceding page.
    while True:
        response = client.list_multipart_uploads(
            Bucket=bucket,
            Prefix=prefix,
            MaxUploads=1000,
        )
        uploads = response.get("Uploads", [])
        if not uploads:
            break
        for upload in uploads:
            client.abort_multipart_upload(
                Bucket=bucket,
                Key=str(upload["Key"]),
                UploadId=str(upload["UploadId"]),
            )
