"""Physical evidence reuse must not weaken closure or lifetime checks."""

from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.version_engine.domain.errors import StorageWriteError
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.mutation_context import publication_context, publication_storage
from src.version_engine.storage.publication import ClosureVerificationError, ClosureVerifier
from src.version_engine.storage.readback_receipts import ReadbackReceipts
from src.version_engine.write_engine.git_object_format import decode_object, encode_object
from tests.repository_hosting.unit.test_streamed_storage import FencedDB, FencedS3

pytestmark = pytest.mark.hosting_component


def graph():
    blob, data = encode_object("blob", b"hello")
    tree, tree_data = encode_object("tree", b"100644 file\0" + bytes.fromhex(blob))
    return tree, {blob: data, tree: tree_data}


def verifier(backend, **kwargs):
    value = ClosureVerifier(backend, **kwargs)
    value.proofs = SimpleNamespace(prefetch=Mock(), get=Mock(return_value=None))
    return value


@pytest.mark.asyncio
async def test_real_readback_once_then_typed_closure_without_more_reads_or_lookups():
    db = FencedDB()
    s3 = FencedS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    root, objects = graph()
    facts = {oid: (decode_object(data)[0], len(decode_object(data)[1])) for oid, data in objects.items()}
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        await backend.async_put_prepared_stream(objects.items(), facts)
        readbacks = len(s3.download_file_keys)
        check = verifier(backend)
        result = check.verify({root: "tree"})
        assert result.objects[root].logical_bytes == result.objects[root].max_blob_bytes == 5
        assert set(result.new_objects) == set(objects)
        assert len(s3.download_file_keys) == readbacks == 1
        check.proofs.get.assert_not_called()
        captured = copy_context()
        with pytest.raises(ClosureVerificationError, match="type mismatch"):
            check.verify({root: "commit"})
        with pytest.raises(ClosureVerificationError, match="byte budget"):
            verifier(backend, max_bytes=1).verify({root: "tree"})
        with pytest.raises(ClosureVerificationError, match="object budget"):
            verifier(backend, max_objects=1).verify({root: "tree"})
    # Copied contexts and later publications cannot reuse expired evidence.
    assert captured.run(check._readback_record, root, None) is None
    with publication_storage("proj", "actor", "another-pin", require_capacity=True):
        assert check._readback_record(root, None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["readback", "index", "settle"])
async def test_incomplete_write_never_promotes_evidence(failure):
    class DB(FencedDB):
        def rpc(self, name, params):
            if (failure == "index" and name.startswith("register")) or (
                    failure == "settle" and name.startswith("settle")):
                return SimpleNamespace(execute=Mock(side_effect=RuntimeError("lost ACK")))
            return super().rpc(name, params)

    class S3(FencedS3):
        def download_file(self, key):
            data = super().download_file(key)
            return data + b"broken" if failure == "readback" else data

    db = DB()
    backend = S3StorageBackend(S3(db), "proj", supabase=db)
    oid, loose = encode_object("blob", b"hello")
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        with pytest.raises(StorageWriteError):
            await backend.async_put_prepared_stream([(oid, loose)], {oid: ("blob", 5)})
        assert not publication_context.get().readbacks.records


@pytest.mark.asyncio
async def test_receipt_does_not_certify_missing_child_or_other_namespace():
    db = FencedDB()
    backend = S3StorageBackend(FencedS3(db), "proj", supabase=db)
    root, objects = graph()
    loose = objects[root]
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        await backend.async_put_prepared_stream([(root, loose)], {root: ("tree", len(decode_object(loose)[1]))})
        read = Mock()
        read.get_durable.side_effect = KeyError("missing child")
        backend.durable_readback = lambda: read
        with pytest.raises(ClosureVerificationError, match="missing child"):
            verifier(backend).verify({root: "tree"})
        other = S3StorageBackend(backend._s3, "other", supabase=db)
        assert verifier(other)._readback_record(root, None) is None
        assert verifier(backend)._readback_record(root, lambda *_: None) is None


@pytest.mark.asyncio
async def test_metadata_budget_uses_durable_fallback(monkeypatch):
    monkeypatch.setattr(ReadbackReceipts, "MAX_BYTES", 0)
    db = FencedDB()
    backend = S3StorageBackend(FencedS3(db), "proj", supabase=db)
    oid, loose = encode_object("blob", b"hello")
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        await backend.async_put_prepared_stream([(oid, loose)], {oid: ("blob", 5)})
        assert not publication_context.get().readbacks.records
        reader = SimpleNamespace(get_durable=Mock(return_value=loose))
        backend.durable_readback = lambda: reader
        assert verifier(backend).verify({oid: "blob"}).objects[oid].logical_bytes == 5
        reader.get_durable.assert_called_once_with(oid)
