"""Real Git pack reads with counted storage transports (not hosted latency)."""

import asyncio
from types import SimpleNamespace

import pytest

from src.version_engine.read.history_cursor import HistoryCursorCodec
from src.version_engine.read.history_graph import HistoryGraphService
from src.version_engine.read.repository_snapshot import repository_snapshot
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.mutation_context import publication_storage
from tests.repository_hosting.integration.test_ref_transaction_service import grant
from tests.repository_hosting.unit.test_direct_object_transport import storage
from tests.repository_hosting.unit.test_standard_git_pack import StreamS3
from tests.repository_hosting.unit.test_standard_git_pack import packed as packed_fixture
from tests.repository_hosting.unit.test_streamed_storage import FencedDB

packed = packed_fixture
pytestmark = pytest.mark.hosting_component


def test_history_pack_downloaded_once_and_warm_graph_downloads_nothing(packed):
    git, _, pack = packed
    db, rpc = FencedDB(), []
    s3 = StreamS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        backend.put_git_pack_durable(pack)
    source, _, pins, _ = storage(git)
    control = source.control
    original = control.begin_read

    def begin(project, actor, pin):
        rpc.append("begin")
        return original(project, actor, pin) | {"project_id": "proj"}

    control.begin_read = begin
    original_release = control.release

    def release(*args):
        rpc.append("release")
        return original_release(*args)

    control.release = release

    def call(name, **params):
        assert name == "get_version_pinned_object_locations_page"
        assert params["p_pin_id"] in pins
        rpc.append("locations")
        return {
            "rows": sorted(db.rows.values(), key=lambda row: row["object_id"]),
            "next_cursor": None,
        }

    control.call = call
    manager = SimpleNamespace(
        open_native_read=lambda project, actor: repository_snapshot(
            control, backend, actor, project_id=project
        )
    )
    history = HistoryGraphService(manager, cursor_codec=HistoryCursorCodec("budget-test"))
    s3.download_file_keys.clear()
    db.select_calls = 0
    first = asyncio.run(history.get_page("proj", limit=80, grant=grant("proj"), summary=True))
    assert len(first.entries) == 2
    assert len(s3.download_file_keys) == 1
    assert db.select_calls == 0
    assert rpc == ["begin", "locations", "release"]
    rpc.clear()
    s3.download_file_keys.clear()
    warm = asyncio.run(history.get_page("proj", limit=80, grant=grant("proj"), summary=True))
    assert warm == first
    assert s3.download_file_keys == [] and rpc == ["begin", "release"]
    detail = asyncio.run(
        history.get_detail("proj", first.entries[0]["commit_id"], grant=grant("proj"))
    )
    assert detail["changes"] == [{"path": "file", "op": "modified"}]
    assert s3.download_file_keys == []  # Shared immutable disk pack, fresh admitted detail.
    assert not pins
    # Ordinary tree reads share this behavior without enumerating all locations.
    rpc.clear()
    with manager.open_native_read("proj", grant("proj")) as snapshot:
        snapshot.revision()
        assert len(snapshot.object(snapshot.revision().tree_oid)[1]) > 0
    assert rpc == ["begin", "release"] and s3.download_file_keys == []
