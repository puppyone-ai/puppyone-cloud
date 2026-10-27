"""Verified object-store snapshot for providers without bucket versioning.

A breaking release normally relies on bucket versioning to recover object bytes
that a data migration overwrites or deletes. Some providers (for example
Supabase Storage) cannot enable versioning. For those environments the release
configuration may explicitly approve ``object_recovery.mode = "snapshot"``: after
writers are stopped, every current object is copied into the separate private
backup bucket, each copy is read back and compared by SHA-256, and a manifest
binds the snapshot. ``restore`` copies a verified snapshot back.

Object bytes, keys and credentials are never logged.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor

CHUNK = 1024 * 1024
MAX_OBJECTS = 1_000_000
MAX_BYTES = 200 * 1024**3
SPOOL_LIMIT = 64 * 1024 * 1024


def _read_hashed(body, sink=None) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    try:
        while chunk := body.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            if sink is not None:
                sink.write(chunk)
    finally:
        body.close()
    return digest.hexdigest(), size


def _inventory(s3, bucket: str, prefix: str = "") -> list[dict]:
    objects, token, total = [], None, 0
    while True:
        args = {"Bucket": bucket, "MaxKeys": 1000}
        if prefix:
            args["Prefix"] = prefix
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for item in page.get("Contents", []):
            objects.append({"key": item["Key"], "size": int(item["Size"])})
            total += int(item["Size"])
        if len(objects) > MAX_OBJECTS or total > MAX_BYTES:
            raise RuntimeError("Object store exceeds the snapshot budget; use versioned storage")
        if not page.get("IsTruncated"):
            return objects
        token = page.get("NextContinuationToken")
        if not token:
            raise RuntimeError("Object listing was truncated without a continuation token")


def _copy_verified(
    s3, source_bucket, source_key, target_bucket, target_key, expected_size, expected_sha256=None
):
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_LIMIT) as spool:
        source = s3.get_object(Bucket=source_bucket, Key=source_key)
        digest, size = _read_hashed(source["Body"], spool)
        if size != expected_size:
            raise RuntimeError("Object changed while writers were stopped")
        # A damaged backup copy must never overwrite the target.
        if expected_sha256 is not None and digest != expected_sha256:
            raise RuntimeError("Snapshot object differs from its manifest")
        spool.seek(0)
        s3.upload_fileobj(spool, target_bucket, target_key)
    copied, copied_size = _read_hashed(s3.get_object(Bucket=target_bucket, Key=target_key)["Body"])
    if copied != digest or copied_size != size:
        raise RuntimeError("Snapshot copy verification failed")
    return digest


def snapshot(s3, *, source_bucket: str, target_bucket: str, prefix: str, workers: int = 8) -> dict:
    """Copy every object in ``source_bucket`` under ``prefix`` and verify each copy."""
    if source_bucket == target_bucket:
        raise ValueError("Object snapshots must use a separate backup bucket")
    prefix = prefix.rstrip("/") + "/"
    objects = _inventory(s3, source_bucket)

    def copy(entry):
        entry = dict(entry)
        entry["sha256"] = _copy_verified(
            s3,
            source_bucket,
            entry["key"],
            target_bucket,
            prefix + "objects/" + entry["key"],
            entry["size"],
        )
        return entry

    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = sorted(pool.map(copy, objects), key=lambda item: item["key"])
    # The source listing is repeated after copying: a changed inventory means a
    # writer was still active, so the snapshot cannot be trusted.
    if [(o["key"], o["size"]) for o in _inventory(s3, source_bucket)] != [
        (e["key"], e["size"]) for e in entries
    ]:
        raise RuntimeError("Object inventory changed during the snapshot")
    manifest = json.dumps(
        {"format_version": 1, "source_bucket": source_bucket, "objects": entries},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    manifest_key = prefix + "objects.manifest.json"
    s3.put_object(Bucket=target_bucket, Key=manifest_key, Body=manifest)
    stored, _ = _read_hashed(s3.get_object(Bucket=target_bucket, Key=manifest_key)["Body"])
    if stored != manifest_sha256:
        raise RuntimeError("Snapshot manifest verification failed")
    return {
        "object_snapshot_ref": f"s3://{target_bucket}/{manifest_key}",
        "object_snapshot_sha256": manifest_sha256,
        "object_snapshot_count": len(entries),
        "object_snapshot_bytes": sum(entry["size"] for entry in entries),
    }


def restore(
    s3,
    *,
    backup_bucket: str,
    manifest_key: str,
    expected_sha256: str,
    target_bucket: str,
    workers: int = 8,
) -> dict:
    """Restore every object of a verified snapshot into ``target_bucket``.

    Objects created after the snapshot are left in place and reported, never
    deleted automatically: removing them is a reviewed operator decision.
    """
    raw = s3.get_object(Bucket=backup_bucket, Key=manifest_key)["Body"]
    try:
        manifest = raw.read()
    finally:
        raw.close()
    if hashlib.sha256(manifest).hexdigest() != expected_sha256:
        raise RuntimeError("Snapshot manifest does not match the recorded release evidence")
    document = json.loads(manifest)
    if document.get("format_version") != 1:
        raise ValueError("Unsupported snapshot manifest")
    prefix = manifest_key.rsplit("objects.manifest.json", 1)[0]

    def put_back(entry):
        _copy_verified(
            s3,
            backup_bucket,
            prefix + "objects/" + entry["key"],
            target_bucket,
            entry["key"],
            entry["size"],
            expected_sha256=entry["sha256"],
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(put_back, document["objects"]))
    known = {entry["key"] for entry in document["objects"]}
    extra = [o["key"] for o in _inventory(s3, target_bucket) if o["key"] not in known]
    return {"restored": len(known), "objects_created_after_snapshot": len(extra)}
