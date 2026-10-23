"""Contract tests exercise the real read session over isolated Git/PG ports."""

import asyncio
import threading
from unittest.mock import Mock

import pytest
from starlette.requests import ClientDisconnect, Request

from src.file_operations.queries import run_read
from src.platform.access.adapters.api import read as api
from src.platform.access.adapters.api.download_token import DownloadTokenClaims
from src.platform.access.adapters.cli import read as cli
from src.platform.access.filesystem.context import ScopedFsContext
from src.platform.access.filesystem.service import ScopedFsService
from tests.repository_hosting.integration.test_ref_transaction_service import grant
from tests.repository_hosting.unit.test_repository_snapshot import row


def test_session_pins_all_reads_and_closes_even_on_failure(native_reads):
    ops, _, wire, calls, _, _, commit, *_ = native_reads
    with (
        pytest.raises(ValueError, match="failed consumer"),
        ops.for_grant(grant("p")).read_session("p") as read,
    ):
        assert read.stat("p", "file")
        # A concurrent ref edit must not change contents or returned head.
        wire["refs"][1] = row(
            b"refs/heads/trunk", {"kind": "oid", "oid": "f" * len(commit)}, "commit"
        )
        assert read.read_file("p", "file") == b"raw\x00bytes"
        assert read.get_head_commit_id("p") == commit
        assert read.get_path_timestamps("p", ["file"])
        with pytest.raises(PermissionError):
            read.read_file("foreign", "file")
        with pytest.raises(PermissionError):
            read.read_file_in_scope("p", "private", "file")
        raise ValueError("failed consumer")
    assert calls.count("begin") == calls.count("release") == 1
    with pytest.raises(RuntimeError, match="closed"):
        read.stat("p", "file")


@pytest.mark.asyncio
async def test_cli_listing_uses_one_snapshot(native_reads, monkeypatch):
    ops, _, _, calls, _, _, commit, *_ = native_reads

    async def admit(*args, **kwargs):
        return "p", {"_runtime_grant": grant("p")}, {"path": "", "mode": "r"}

    monkeypatch.setattr(cli, "_resolve_auth", admit)
    result = await cli.list_dir(
        path="dir", include_hidden=False, include_size=False, include_times=True, ops=ops
    )
    assert result.data["head_commit_id"] == commit
    assert [e["name"] for e in result.data["entries"]] == ["inside"]
    assert calls.count("begin") == calls.count("release") == 1


@pytest.mark.asyncio
async def test_scoped_fs_cat_uses_one_snapshot(native_reads):
    ops, _, _, calls, *_ = native_reads
    service = ScopedFsService(ops.for_grant(grant("p")), None)
    ctx = ScopedFsContext(
        api_key="test",
        endpoint_id="e",
        endpoint_name="e",
        project_id="p",
        user_id="u",
        scope_id="",
        scope_path="",
        mode="ro",
        exclude=[],
    )
    result = await service.cat(ctx, "file")
    assert result["content_text"] == "raw\x00bytes"
    assert calls.count("begin") == calls.count("release") == 1


@pytest.mark.asyncio
async def test_read_does_not_block_loop_and_cancellation_joins_before_release(native_reads):
    ops, _, _, calls, *_ = native_reads
    entered, finish = threading.Event(), threading.Event()

    def query(read):
        read.stat("p", "file")
        entered.set()
        assert finish.wait(2)

    task = asyncio.create_task(run_read(ops.for_grant(grant("p")), "p", query))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set(), "event loop was blocked"
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert "release" not in calls
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls.count("begin") == calls.count("release") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_download_authorizes_and_owns_snapshot_until_stream_end(
    native_reads, monkeypatch, disconnect
):
    ops, _, _, calls, *_ = native_reads
    monkeypatch.setattr(
        api, "verify_token", lambda _: DownloadTokenClaims("p", "dir", "u", 9999999999)
    )
    authorization = Mock()
    authorization.authorize.return_value = grant("p")
    request = Request({"type": "http", "headers": []})
    response = api.download(
        "p", request, path="dir", token="signed", ops=ops, authorization=authorization
    )
    authorization.authorize.assert_called_once()
    assert calls.count("begin") == 1 and "release" not in calls
    body = []

    async def send(message):
        if message["type"] == "http.response.body":
            if disconnect:
                raise OSError("disconnected")
            body.append(message["body"])

    async def receive():
        await asyncio.sleep(10)

    if disconnect:
        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    else:
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(b"".join(body))) as archive:
            assert archive.read("dir/inside") == b"raw\x00bytes"
    assert calls.count("begin") == calls.count("release") == 1


def test_download_revoked_token_user_never_opens_storage(native_reads, monkeypatch):
    ops, _, _, calls, *_ = native_reads
    monkeypatch.setattr(
        api, "verify_token", lambda _: DownloadTokenClaims("p", "dir", "u", 9999999999)
    )
    authorization = Mock()
    authorization.authorize.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError, match="revoked"):
        api.download(
            "p",
            Request({"type": "http", "headers": []}),
            path="dir",
            token="signed",
            ops=ops,
            authorization=authorization,
        )
    assert calls == []


def test_read_session_budget_does_not_grow_with_file_count(native_reads):
    ops, _, _, calls, *_ = native_reads
    with ops.for_grant(grant("p")).read_session("p") as read:
        for _ in range(35):
            assert read.read_file("p", "dir/inside") == b"raw\x00bytes"
    assert calls.count("begin") == calls.count("release") == 1
    # commit + root tree + child tree + blob, reused across the operation.
    assert calls.count("get") == 4
