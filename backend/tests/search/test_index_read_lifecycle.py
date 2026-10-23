"""Actual index orchestration over native snapshots; external ports are fake."""

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.infra.chunking.config import ChunkingConfig
from src.infra.search import service as search_module
from src.infra.search.service import SearchService
from src.version_engine.read.native_tree_reader import NativeTreeReader
from src.version_engine.write_engine.git_object_format import TreeEntry, encode_object, encode_tree
from tests.authorization_fakes import authorization_for
from tests.repository_hosting.unit.test_repository_snapshot import row


@pytest.fixture
def index_case(native_reads, monkeypatch):
    ops, manager, wire, calls, objects, *_ = native_reads
    fmt = wire["object_format"]

    def put(kind, body):
        oid, raw = encode_object(kind, body, object_format=fmt)
        objects[oid] = raw
        return oid

    bodies = {f"file-{i:02}.md": f"original file {i}".encode() for i in range(35)}
    bodies["scope.json"] = b'{"text":"original JSON"}'
    root = put("tree", encode_tree(
        [TreeEntry(path, b"100644", put("blob", body)) for path, body in bodies.items()],
        object_format=fmt,
    ))
    commit = put("commit", f"tree {root}\nauthor A <a@local> 1 +0000\ncommitter A <a@local> 1 +0000\n\nindex\n".encode())
    wire["refs"][1] = row(b"refs/heads/trunk", {"kind": "oid", "oid": commit}, "commit")
    events = []
    original = NativeTreeReader.read_file
    loop_thread = threading.get_ident()

    def read(reader, project, path):
        assert threading.get_ident() != loop_thread
        events.append(("read", path))
        return original(reader, project, path)

    monkeypatch.setattr(NativeTreeReader, "read_file", read)
    monkeypatch.setattr(ProductOperationAdapter, "for_user", Mock(side_effect=AssertionError("per-file authorization")))
    monkeypatch.setattr(ProductOperationAdapter, "_read", Mock(side_effect=AssertionError("one-shot read")))
    authorization = authorization_for("p")
    authorization.authorize_fresh = Mock(wraps=authorization.authorize_fresh)

    def chunks(**kwargs):
        return SimpleNamespace(chunks=[SimpleNamespace(
            id=1, path=kwargs["path"], chunk_text=kwargs["content"], json_pointer=kwargs["json_pointer"],
            content_hash="hash", chunk_index=0, total_chunks=1, char_start=0, char_end=20,
        )])

    monkeypatch.setattr(search_module, "ensure_chunks_for_pointer", chunks)

    async def embed(texts):
        events.append(("embed", texts[0]))
        # Later reads must not follow this now-invalid HEAD.
        wire["refs"][1] = row(b"refs/heads/trunk", {"kind": "oid", "oid": "f" * len(commit)}, "commit")
        await asyncio.sleep(0)
        return [[0.1] for _ in texts]

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield

    embedding = SimpleNamespace(generate_embeddings_batch=AsyncMock(side_effect=embed))
    vectors = SimpleNamespace(write=AsyncMock())
    service = SearchService(
        ops=ops, authorization=authorization, chunk_repo=Mock(),
        embedding_service=embedding, turbopuffer_service=vectors,
        chunking_config=ChunkingConfig(chunk_threshold_chars=1), write_lease_factory=lease,
    )
    return SimpleNamespace(
        service=service, auth=authorization, embedding=embedding, vectors=vectors,
        calls=calls, manager=manager, events=events, bodies=bodies,
    )


@pytest.mark.asyncio
async def test_folder_indexes_distinct_files_with_one_grant_and_snapshot(index_case):
    case = index_case
    stats = await case.service.index_folder(project_id="p", user_id="u", folder_path="", s3_service=None)
    assert stats.indexed_files == stats.total_files == 36
    assert case.auth.authorize_fresh.call_count == 1
    assert case.calls.count("begin") == case.calls.count("release") == 1
    assert case.vectors.write.await_count == 36
    # No eager whole-repository content fetch: each read is consumed before next.
    assert [kind for kind, _ in case.events] == ["read", "embed"] * 36
    assert all("original" in texts.args[0][0] for texts in case.embedding.generate_embeddings_batch.await_args_list)


@pytest.mark.asyncio
async def test_single_json_scope_uses_the_same_owned_pipeline(index_case):
    case = index_case
    stats = await case.service.index_scope(project_id="p", user_id="u", path="scope.json", json_path="")
    assert stats.indexed_chunks_count == 1
    assert case.auth.authorize_fresh.call_count == 1
    assert case.calls.count("begin") == case.calls.count("release") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["source", "embedding", "vector", "cancel"])
async def test_failed_index_never_returns_partial_success_and_closes_pin(index_case, monkeypatch, failure):
    case = index_case
    if failure == "source":
        monkeypatch.setattr(NativeTreeReader, "read_file", Mock(side_effect=RuntimeError("source unavailable")))
    elif failure == "vector":
        case.vectors.write.side_effect = RuntimeError("vector unavailable")
    else:
        case.embedding.generate_embeddings_batch.side_effect = (
            asyncio.CancelledError() if failure == "cancel" else RuntimeError("embedding unavailable")
        )
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await case.service.index_folder(project_id="p", user_id="u", folder_path="", s3_service=None)
    assert case.calls.count("begin") == case.calls.count("release") == 1
    if failure in {"source", "embedding", "cancel"}:
        case.vectors.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_project_is_denied_before_read_or_embedding(index_case):
    from src.exceptions import NotFoundException

    case = index_case
    with pytest.raises(NotFoundException):
        await case.service.index_folder(project_id="foreign", user_id="u", folder_path="", s3_service=None)
    case.manager.open_native_read.assert_not_called()
    case.embedding.generate_embeddings_batch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"{invalid"])
async def test_invalid_json_is_not_a_successful_empty_index(index_case, monkeypatch, body):
    case = index_case
    monkeypatch.setattr(NativeTreeReader, "read_file", Mock(return_value=body))
    with pytest.raises(ValueError, match="Invalid JSON index source"):
        await case.service.index_scope(project_id="p", user_id="u", path="scope.json", json_path="")
    case.vectors.write.assert_not_awaited()
    assert case.calls.count("begin") == case.calls.count("release") == 1


@pytest.mark.asyncio
async def test_failure_after_one_file_is_still_a_failed_job(index_case):
    case = index_case
    case.vectors.write.side_effect = [None, RuntimeError("second file failed")]
    with pytest.raises(RuntimeError, match="second file failed"):
        await case.service.index_folder(project_id="p", user_id="u", folder_path="", s3_service=None)
    assert case.vectors.write.await_count == 2
    assert len(case.events) == 4
    assert case.calls.count("begin") == case.calls.count("release") == 1


@pytest.mark.asyncio
async def test_cancelling_chunk_persistence_joins_worker_before_releasing_pin(index_case, monkeypatch):
    case = index_case
    entered, finish = threading.Event(), threading.Event()
    original = search_module.ensure_chunks_for_pointer

    def block(**kwargs):
        entered.set()
        assert finish.wait(3)
        return original(**kwargs)

    monkeypatch.setattr(search_module, "ensure_chunks_for_pointer", block)
    task = asyncio.create_task(case.service.index_folder(
        project_id="p", user_id="u", folder_path="", s3_service=None,
    ))
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert "release" not in case.calls
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert case.calls.count("begin") == case.calls.count("release") == 1
    case.vectors.write.assert_not_awaited()
