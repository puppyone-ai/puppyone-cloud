"""Bounded, publication-local evidence from a completed physical write.

These facts are not transitive closure proofs or a byte cache. The closure
walker still checks edges and budgets, and SQL still fences publication. Only
the prepared S3 writer promotes facts after readback, index ACK and settlement.
"""

import hashlib
import json
import threading

from src.version_engine.write_engine.git_object_format import decode_object, hash_object
from src.version_engine.write_engine.git_object_graph import object_edges


class ReadbackReceipts:
    MAX_BYTES = 16 * 1024**2
    MAX_OBJECTS = 100_000

    def __init__(self):
        self.records = {}
        self.bytes = 0
        self.active = True

    def close(self):
        self.active = False
        self.records.clear()
        self.bytes = 0

    def promote(self, batch):
        if not self.active:
            raise RuntimeError("publication readback context expired")
        for oid, (record, placement, size) in batch.records.items():
            if oid in self.records:
                continue
            if len(self.records) >= self.MAX_OBJECTS or self.bytes + size > self.MAX_BYTES:
                break  # The durable reader remains the bounded fallback.
            self.records[oid] = (record, placement)
            self.bytes += size

    def get(self, oid):
        if not self.active:
            return None
        entry = self.records.get(oid)
        return entry[0] if entry is not None else None


class PreparedReadback:
    """Readback evidence for one ingestion; batches verify on parallel threads."""

    def __init__(self, *, max_bytes=ReadbackReceipts.MAX_BYTES):
        self.records = {}
        self.bytes = 0
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def verify(self, oid, loose):
        kind, body = decode_object(loose)
        return self.verify_raw(oid, kind, body)

    def verify_raw(self, oid, kind, body):
        from src.version_engine.storage.publication import VerifiedObject

        fmt = "sha256" if len(oid) == 64 else "sha1"
        if hash_object(kind, body, object_format=fmt) != oid:
            raise ValueError("incoming object hash mismatch")
        edges = tuple(object_edges(kind, body, object_format=fmt))
        digest = hashlib.sha256(f"{kind} {len(body)}\0".encode() + body).hexdigest()
        record = VerifiedObject(kind, len(body), digest, edges)
        # Include space for placement metadata; no object bytes are retained.
        size = len(json.dumps([oid, kind, len(body), digest, edges]).encode()) + 512
        with self._lock:  # the byte/object budget is shared by all batch threads
            if (size <= 2 * 1024**2 and self.bytes + size <= self.max_bytes
                    and len(self.records) < ReadbackReceipts.MAX_OBJECTS):
                self.records[oid] = (record, None, size)
                self.bytes += size
        return kind, len(body)

    def placements(self, rows):
        with self._lock:
            for row in rows:
                oid = row["object_id"]
                if oid in self.records:
                    record, _, size = self.records[oid]
                    self.records[oid] = (record, (
                        row["pack_key"], row["offset_bytes"], row["size_bytes"],
                    ), size)

    def finish(self):
        if any(placement is None for _, placement, _ in self.records.values()):
            raise RuntimeError("readback evidence has no acknowledged placement")
        return self
