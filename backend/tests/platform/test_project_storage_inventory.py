from __future__ import annotations

import pytest

from src.platform.project.storage_inventory import (
    InventoryProof,
    observe_project_storage_inventory,
    parse_legacy_project_storage_key,
    run_project_storage_inventory,
)


class S3InventoryStub:
    def __init__(self) -> None:
        self.multipart_calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        if not kwargs.get("ContinuationToken"):
            return {
                "Contents": [
                    {"Key": "users/user-1/raw/project-1/input.pdf"},
                    {"Key": "users/unowned/misc/value"},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "objects-page-2",
            }
        return {
            "Contents": [
                {"Key": "users/user-2/processed/project-2/result.json"}
            ],
            "IsTruncated": False,
        }

    def list_multipart_uploads(self, **kwargs):
        self.multipart_calls.append(kwargs)
        if not kwargs.get("KeyMarker"):
            return {
                "Uploads": [
                    {
                        "Key": "users/user-1/etl_artifacts/project-1/large.bin",
                        "UploadId": "upload-1",
                    }
                ],
                "IsTruncated": True,
                "NextKeyMarker": "next-key",
                "NextUploadIdMarker": "next-upload",
            }
        return {"Uploads": [], "IsTruncated": False}


class RepositoryStub:
    def __init__(self) -> None:
        self.batches: list[dict] = []
        self.finalized: list[InventoryProof] = []
        self.verified: list[InventoryProof] = []
        self.completed = 0
        self.cleaned: list[tuple[str, str]] = []
        self.saved_checkpoint: dict = {}

    def checkpoint(self):
        return dict(self.saved_checkpoint)

    def record_batch(self, **kwargs):
        self.batches.append(kwargs)
        self.saved_checkpoint = dict(kwargs["checkpoint"])
        return {"outcome": "recorded"}

    def finalize_scan(self, proof):
        self.finalized.append(proof)
        return {"outcome": "finalized"}

    def verify_scan(self, proof):
        self.verified.append(proof)
        return {"outcome": "verified"}

    def complete(self):
        self.completed += 1
        return {"outcome": "completed"}

    def live_project_ids(self):
        return {"project-1", "project-2"}

    def pending_orphans(self):
        return []

    def mark_orphan_cleaned(self, project_id, principal):
        self.cleaned.append((project_id, principal))


def test_legacy_key_parser_accepts_only_exact_owned_namespaces() -> None:
    assert parse_legacy_project_storage_key(
        "users/user-1/raw/project-1/file.pdf"
    ) == ("project-1", "user-1")
    assert parse_legacy_project_storage_key(
        "users/user-1/processed/project-1/value"
    ) == ("project-1", "user-1")
    assert parse_legacy_project_storage_key("users/user-1/misc/project-1/value") is None
    assert parse_legacy_project_storage_key(
        "users/../raw/project-1/value"
    ) is None


def test_inventory_uses_both_multipart_markers_and_has_stable_proof() -> None:
    client = S3InventoryStub()

    first = observe_project_storage_inventory(client, "bucket")
    second = observe_project_storage_inventory(client, "bucket")

    assert first == second
    assert first.object_count == 3
    assert first.multipart_count == 1
    assert len(first.digest) == 64
    second_page = next(call for call in client.multipart_calls if call.get("KeyMarker"))
    assert second_page["KeyMarker"] == "next-key"
    assert second_page["UploadIdMarker"] == "next-upload"


def test_apply_records_idempotent_pages_then_requires_independent_second_pass() -> None:
    repository = RepositoryStub()

    proof = run_project_storage_inventory(
        S3InventoryStub(),
        "bucket",
        repository,  # type: ignore[arg-type]
        apply=True,
    )

    assert len(repository.batches) == 4
    assert {tuple(sorted(item.items())) for batch in repository.batches for item in batch["principals"]} == {
        (("principal", "user-1"), ("project_id", "project-1")),
        (("principal", "user-2"), ("project_id", "project-2")),
    }
    assert repository.finalized == repository.verified == [proof]
    assert repository.completed == 1


def test_truncated_multipart_page_without_upload_marker_fails_closed() -> None:
    client = S3InventoryStub()

    def malformed(**_kwargs):
        return {
            "Uploads": [],
            "IsTruncated": True,
            "NextKeyMarker": "key-only",
        }

    client.list_multipart_uploads = malformed  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="both KeyMarker and UploadIdMarker"):
        observe_project_storage_inventory(client, "bucket")


def test_unknown_project_prefix_blocks_dry_run_completion() -> None:
    repository = RepositoryStub()
    repository.live_project_ids = lambda: {"project-2"}  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="unknown Project prefixes"):
        run_project_storage_inventory(
            S3InventoryStub(),
            "bucket",
            repository,  # type: ignore[arg-type]
            apply=False,
        )


class OrphanS3Stub:
    def __init__(self) -> None:
        self.keys = {"users/user-9/raw/deleted-project/file.pdf"}
        self.uploads = {
            ("users/user-9/processed/deleted-project/large.bin", "upload-9")
        }

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {
            "Contents": [
                {"Key": key} for key in sorted(self.keys) if key.startswith(prefix)
            ],
            "IsTruncated": False,
        }

    def delete_objects(self, **kwargs):
        for item in kwargs["Delete"]["Objects"]:
            self.keys.discard(item["Key"])
        return {"Errors": []}

    def list_multipart_uploads(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {
            "Uploads": [
                {"Key": key, "UploadId": upload_id}
                for key, upload_id in sorted(self.uploads)
                if key.startswith(prefix)
            ],
            "IsTruncated": False,
        }

    def abort_multipart_upload(self, **kwargs):
        self.uploads.discard((kwargs["Key"], kwargs["UploadId"]))


def test_apply_purges_and_verifies_unknown_project_prefix_before_enabling() -> None:
    repository = RepositoryStub()
    repository.live_project_ids = lambda: set()  # type: ignore[method-assign]
    repository.pending_orphans = lambda: [  # type: ignore[method-assign]
        ("deleted-project", "user-9")
    ]
    client = OrphanS3Stub()

    proof = run_project_storage_inventory(
        client,
        "bucket",
        repository,  # type: ignore[arg-type]
        apply=True,
    )

    assert proof.object_count == proof.multipart_count == 0
    assert not client.keys and not client.uploads
    assert repository.cleaned == [("deleted-project", "user-9")]
    assert repository.completed == 1


def test_inventory_resume_reads_checkpoint_and_starts_at_saved_page() -> None:
    client = S3InventoryStub()
    seen: list[tuple[str, dict]] = []

    proof = observe_project_storage_inventory(
        client,
        "bucket",
        checkpoint={
            "phase": "objects",
            "objects_complete": False,
            "continuation_token": "objects-page-2",
        },
        on_page=lambda kind, _entries, _principals, checkpoint: seen.append(
            (kind, checkpoint)
        ),
    )

    assert proof.object_count == 1
    assert proof.multipart_count == 1
    assert seen[-1][1]["phase"] == "complete"


def test_orphan_cleanup_rejects_s3_per_object_delete_errors() -> None:
    repository = RepositoryStub()
    repository.live_project_ids = lambda: set()  # type: ignore[method-assign]
    repository.pending_orphans = lambda: [  # type: ignore[method-assign]
        ("deleted-project", "user-9")
    ]
    client = OrphanS3Stub()

    def rejected(**_kwargs):
        return {
            "Errors": [
                {
                    "Key": "users/user-9/raw/deleted-project/file.pdf",
                    "Code": "AccessDenied",
                }
            ]
        }

    client.delete_objects = rejected  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="per-object failures"):
        run_project_storage_inventory(
            client,
            "bucket",
            repository,  # type: ignore[arg-type]
            apply=True,
        )
    assert repository.cleaned == []
