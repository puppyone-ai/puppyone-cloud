"""Count real ingestion calls for a supplied full Git pack using local doubles.

Run from backend:
  python -m tests.repository_hosting.harness.ingestion_benchmark --pack input.pack

No source repository or network service is mutated. Timings exclude Git pack
generation, authentication, real PG/S3, closure verification and ref publication;
they are NOT full initialization acceptance or a claim about hosted latency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

# Deliberately never load developer credentials for this local component probe.
os.environ["SUPABASE_URL"] = "http://127.0.0.1:1"
os.environ["SUPABASE_KEY"] = "local-ingestion-benchmark"
os.environ["INTERNAL_API_SECRET"] = "local-ingestion-benchmark"
os.environ["APP_ENV"] = "test"
os.environ["MANAGED_AI_ENABLED"] = "false"
os.environ["VERSION_TRACE_ENABLED"] = "false"

from src.platform.access.adapters.git.object_pack import IncomingPack
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.mutation_context import publication_storage
from src.version_engine.write_engine.git_object_format import encode_object
from src.version_engine.write_engine.trace import VersionTrace, use_version_trace
from tests.repository_hosting.unit.test_streamed_storage import FencedDB, FencedS3


class EmptyReader:
    format = "sha1"
    snapshot = SimpleNamespace(check_live=lambda: None)

    def authorize(self, _oids):
        raise ValueError("Benchmark requires a full pack, without external thin bases")

    def get(self, _oid):
        raise ValueError("Benchmark requires a full pack, without external thin bases")


def measure(incoming, *, mode):
    db = FencedDB()
    s3 = FencedS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    trace = VersionTrace("object-ingestion-benchmark", enabled=False)

    def objects():
        empty_oid, empty_loose = encode_object("tree", b"")
        yield empty_oid, empty_loose
        for oid in sorted(incoming.objects):
            if oid != empty_oid:
                actual, data = encode_object(*incoming.get(oid))
                assert actual == oid
                yield oid, data

    started = time.perf_counter()
    with (
        publication_storage("proj", "actor", "pin", require_capacity=True),
        use_version_trace(trace),
    ):
        if mode == "legacy":
            batch, size = {}, 0
            for oid, data in objects():
                if batch and (len(batch) >= 100 or size + len(data) > 8 * 1024**2):
                    backend.put_objects(batch)
                    batch, size = {}, 0
                batch[oid] = data
                size += len(data)
            if batch:
                backend.put_objects(batch)
        elif mode == "byte_windows":
            backend.put_stream(objects())
        else:
            facts = {oid: (kind, size) for oid, (kind, _offset, size) in incoming.objects.items()}
            facts[encode_object("tree", b"")[0]] = ("tree", 0)
            backend.put_prepared_stream_durable(objects(), facts)
    duration = time.perf_counter() - started
    summaries = [p for p in trace.phases if p["phase"] == "storage.stream.summary"]
    result = {
        "component_seconds": round(duration, 4),
        "write_windows": sum(p["batches"] for p in summaries),
        "io_operations": len(db.reservations),
        "settled_operations": len(db.settled),
        "s3_puts": len(s3.upload_sizes),
        "s3_readbacks": len(s3.download_file_keys),
        "s3_put_bytes": sum(s3.upload_sizes),
        "db_calls": dict(Counter(db.calls)),
        "peak_simultaneous_puts": s3.peak,
        "peak_inflight_encoded_bytes": max(p["peak_inflight_bytes"] for p in summaries),
    }
    cold = S3StorageBackend(s3, "proj", supabase=db)
    checked = 0
    for oid, expected in objects():
        assert cold.get(oid) == expected
        checked += 1
    result["cold_read_objects_verified"] = checked
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.pack.open("rb") as handle:
        pack_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    with IncomingPack(EmptyReader()) as incoming:
        start = time.perf_counter()
        with args.pack.open("rb") as handle:
            incoming.read(handle)
        result = {
            "conditions": "Local S3/PG doubles; 2ms artificial PUT yield; no network or final publication; timings are component-only.",
            "pack_bytes": args.pack.stat().st_size,
            "pack_sha256": pack_digest,
            "pack_objects": len(incoming.objects),
            "parse_seconds": round(time.perf_counter() - start, 4),
            "legacy_100_object_windows": measure(incoming, mode="legacy"),
            "byte_windows": measure(incoming, mode="byte_windows"),
            "prepared_operation": measure(incoming, mode="prepared"),
        }
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded)


if __name__ == "__main__":
    main()
