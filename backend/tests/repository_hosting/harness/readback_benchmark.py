"""Compare closure I/O with/without request-local evidence, using local doubles.

No hosted traffic, publication, or end-to-end timing claim. Run from backend:
python -m tests.repository_hosting.harness.readback_benchmark --pack ... --root ...
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

# Never load developer credentials for this component probe.
os.environ.update(SUPABASE_URL="http://127.0.0.1:1", SUPABASE_KEY="local-ingestion-benchmark",
                  INTERNAL_API_SECRET="local-ingestion-benchmark", APP_ENV="test",
                  MANAGED_AI_ENABLED="false", VERSION_TRACE_ENABLED="false")

from src.platform.access.adapters.git.object_pack import IncomingPack
from src.version_engine.infrastructure.supabase.object_proofs import ObjectProofRepository
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.mutation_context import publication_context, publication_storage
from src.version_engine.storage.publication import ClosureVerifier
from src.version_engine.write_engine.git_object_format import encode_object

# Imports the explicit local-only environment before constructing adapters.
from tests.repository_hosting.harness.ingestion_benchmark import EmptyReader
from tests.repository_hosting.unit.test_streamed_storage import FencedDB, FencedS3


async def measure(incoming, root, reuse):
    db = FencedDB()
    s3 = FencedS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    proof_calls = []

    def proof_lookup(name, **params):
        assert name == "get_version_object_proofs"
        proof_calls.append(len(params["p_oids"]))
        return {}

    def objects():
        yield encode_object("tree", b"")
        for oid in sorted(incoming.objects):
            if oid != encode_object("tree", b"")[0]:
                yield encode_object(*incoming.get(oid))

    facts = {oid: (kind, size) for oid, (kind, _, size) in incoming.objects.items()}
    facts[encode_object("tree", b"")[0]] = ("tree", 0)
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        await backend.async_put_prepared_stream(objects(), facts)
        evidence = publication_context.get().readbacks
        retained = {"objects": len(evidence.records), "metadata_budget_bytes": evidence.bytes}
        if not reuse:
            evidence.close()
        before = (len(s3.download_file_keys), len(s3.download_range_keys), db.select_calls)
        verifier = ClosureVerifier(backend)
        verifier.proofs = ObjectProofRepository(SimpleNamespace(call=proof_lookup), "proj", "actor", "pin", "sha1")
        start = time.perf_counter()
        manifest = await asyncio.to_thread(verifier.verify, {root: "commit"})
        duration = time.perf_counter() - start
        return {
            "closure_component_seconds": round(duration, 4),
            "closure_objects": len(manifest.objects), "manifest_sha256": manifest.digest,
            "upload_puts": len(s3.upload_sizes), "upload_readback_gets": before[0],
            "closure_whole_gets": len(s3.download_file_keys) - before[0],
            "closure_range_gets": len(s3.download_range_keys) - before[1],
            "closure_location_queries": db.select_calls - before[2],
            "closure_proof_queries": len(proof_calls), "retained": retained,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.pack.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    with IncomingPack(EmptyReader()) as incoming:
        with args.pack.open("rb") as file:
            incoming.read(file)
        result = {"conditions": "Local S3/PG doubles; no network, final publication or Desktop; component-only.",
                  "pack_sha256": digest, "root": args.root,
                  "before": asyncio.run(measure(incoming, args.root, False)),
                  "after": asyncio.run(measure(incoming, args.root, True))}
    assert result["before"]["manifest_sha256"] == result["after"]["manifest_sha256"]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
