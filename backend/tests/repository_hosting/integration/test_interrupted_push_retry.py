"""No resumable upload state: retry against the existing PG ref authority.

Real PostgreSQL and durable disk objects; this does not claim S3 acceptance.
Both initial and incremental publication use the same service/SQL contract.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI, Request

from src.infra.execution.context import Execution, current_execution
from src.platform.access.adapters.git import router
from src.platform.access.adapters.git.native import NativeGitEndpoint
from src.platform.access.adapters.git.native_wire import receive_commands
from src.version_engine.write_engine.git_object_format import encode_object
from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.harness.http_server import _serve_git_app
from tests.repository_hosting.integration.test_ref_transaction_service import (
    grant,
    request,
)
from tests.repository_hosting.integration.test_ref_transaction_service import (
    publisher as publisher_fixture,
)

pytestmark = pytest.mark.hosting_live
publisher = publisher_fixture


@pytest.mark.parametrize(("incremental", "initial_header"), [
    (False, False), (False, True), (True, False),
], ids=["initial-stock-git", "initial-desktop", "incremental-stock-git"])
def test_oversized_push_preserves_refs_objects_and_local_files(
    publisher, monkeypatch, incremental, initial_header
):
    """Real HTTP/Git/PG, with a small cap to exercise rejection without 2 GiB I/O."""
    _, auth, backend, service, git, first, prepare = publisher
    if incremental:
        assert request(service, first, prepare)["status"] == "committed"
    candidate = git.commit({"new-file": b"new committed contents"})
    (git.path / "uncommitted.txt").write_bytes(b"keep local work")
    before = service.control.snapshot(auth.project)
    durable_before = set(backend.all_hashes())
    transactions_before = auth.count("version_ref_transactions")
    endpoint = NativeGitEndpoint(service, grant(auth.project), None)
    receive = endpoint.receive
    spools = []

    def track_receive(path, **kwargs):
        spools.append(path)
        return receive(path, **kwargs)

    monkeypatch.setattr(endpoint, "receive", track_receive)
    monkeypatch.setattr(router, "_git_receive_max_body_bytes", lambda _: 64)
    app = FastAPI()

    @app.get("/repo.git/info/refs")
    def info(service: str, request: Request):
        return endpoint.info_refs(service, protocol=request.headers.get("git-protocol", ""))

    @app.post("/repo.git/git-receive-pack")
    async def rpc(request: Request):
        return await router._native_rpc(endpoint, request, upload=False, project_id=auth.project)

    options = ["-c", f"http.extraHeader=X-PuppyOne-Initial-Attempt: {uuid.uuid4()}"] if initial_header else []
    with _serve_git_app(app) as address:
        failed = git.run(*options, "push", address + "/repo.git", "main", check=False)
    assert failed.returncode != 0 and b"64-byte limit" in failed.stderr
    after = service.control.snapshot(auth.project)
    assert after["refs"] == before["refs"]
    assert after["ref_sequence"] == before["ref_sequence"]
    assert auth.count("version_ref_transactions") == transactions_before
    assert set(backend.all_hashes()) == durable_before
    assert len(spools) == 1 and not spools[0].exists()
    assert git.text("rev-parse", "HEAD") == candidate
    assert (git.path / "uncommitted.txt").read_bytes() == b"keep local work"


@pytest.mark.parametrize("incremental", [False, True], ids=["initial", "incremental"])
def test_interrupted_upload_can_retry_without_erasing_repository(publisher, incremental):
    _, auth, backend, service, git, first, prepare = publisher
    old = first if incremental else None
    if incremental:
        assert request(service, first, prepare)["status"] == "committed"
    candidate = git.commit({"retry": b"immutable candidate"})
    original_key = str(uuid.uuid4())
    new_objects = {
        oid: encode_object(kind, body)[1]
        for oid, (kind, body) in git.objects().items()
        if not backend.exists(oid)
    }
    assert len(new_objects) > 1
    partial = next(iter(new_objects))

    def interrupted():
        backend.put_durable(partial, new_objects[partial])
        raise ConnectionError("storage failed after only one object became durable")

    with pytest.raises(ConnectionError):
        request(service, candidate, interrupted, key=original_key, old=old)
    assert (auth.state() or {}).get("oid") == old
    assert service.control.result(auth.project, "user:fixture-user", original_key) is None
    assert backend.exists(partial)
    assert any(not backend.exists(oid) for oid in new_objects)

    # Retrying must also work when unpublished bytes are no longer available.
    # This is test fault injection, not a client-authorized production cleanup.
    backend.delete(partial)
    retry = request(service, candidate, prepare, old=old)
    assert retry["status"] == "committed"
    # A delayed original execution cannot overwrite the winning retry.
    late = request(service, candidate, prepare, key=original_key, old=old)
    assert late["status"] == "rejected" and late["reason"] == "stale_ref"
    assert auth.state()["oid"] == candidate
    assert service.control.snapshot(auth.project)["ref_sequence"] == (2 if incremental else 1)


@pytest.mark.parametrize("incremental", [False, True], ids=["initial", "incremental"])
def test_stock_http_push_restarts_a_truncated_pack_and_preserves_published_history(
    publisher, tmp_path, monkeypatch, incremental
):
    _, auth, backend, service, git, first, prepare = publisher
    old = first if incremental else None
    if incremental:
        assert request(service, first, prepare)["status"] == "committed"
    candidate = git.commit({"retry": b"contents to send again"})
    (git.path / "uncommitted.txt").write_bytes(b"keep local work")
    durable_before = set(backend.all_hashes())
    transactions_before = auth.count("version_ref_transactions")
    endpoint = NativeGitEndpoint(service, grant(auth.project), None)
    receive = endpoint.receive
    packs, spools = [], []

    def truncate_first_pack(path, **kwargs):
        with path.open("rb") as handle:
            receive_commands(handle, service.object_format)
            offset = handle.tell()
            packs.append(handle.read())
        spools.append(path)
        if len(packs) == 1:
            # Deterministic transport fault: the real parser sees only half of
            # stock Git's pack, without unplugging the developer's network.
            with path.open("r+b") as handle:
                handle.truncate(offset + len(packs[-1]) // 2)
        return receive(path, **kwargs)

    monkeypatch.setattr(endpoint, "receive", truncate_first_pack)
    monkeypatch.setattr(router, "_git_receive_max_body_bytes", lambda _: 128 * 1024**2)
    app = FastAPI()

    @app.get("/repo.git/info/refs")
    def info(service: str, request: Request):
        return endpoint.info_refs(service, protocol=request.headers.get("git-protocol", ""))

    @app.post("/repo.git/{command}")
    async def rpc(command: str, request: Request):
        assert command in {"git-upload-pack", "git-receive-pack"}
        return await router._native_rpc(
            endpoint, request, upload=command == "git-upload-pack", project_id=auth.project
        )

    with _serve_git_app(app) as address:
        remote = address + "/repo.git"
        command = ("-c", "pack.threads=1", "push", remote, "main")
        failed = git.run(*command, check=False)
        assert failed.returncode != 0 and b"unpack" in failed.stderr
        assert (auth.state() or {}).get("oid") == old
        assert auth.count("version_ref_transactions") == transactions_before
        assert set(backend.all_hashes()) == durable_before
        assert not spools[0].exists()

        git.run(*command)
        assert auth.state()["oid"] == candidate
        assert service.control.snapshot(auth.project)["ref_sequence"] == (2 if incremental else 1)
        assert auth.count("version_ref_transactions") == transactions_before + 1
        # Both requests start at PACK, with the same complete proposed content.
        # There is no offset/session token that depends on the failed spool.
        assert len(packs) == 2 and packs[0] == packs[1]
        assert packs[1].startswith(b"PACK")
        assert all(not path.exists() for path in spools)
        expected = git.text("rev-list", "--objects", candidate, *(["^" + old] if old else []))
        assert int.from_bytes(packs[1][8:12], "big") == len(expected.splitlines())

        git.run("clone", "--mirror", remote, tmp_path / "verified.git")
        clone = Git(tmp_path / "verified.git")
        clone.run("fsck", "--full", "--strict")
        assert clone.text("rev-parse", "main") == candidate
        assert clone.objects() == git.objects()
        git.run(*command)
        assert len(packs) == 2  # An already-published target sends no third pack.

    assert git.text("rev-parse", "HEAD") == candidate
    assert (git.path / "uncommitted.txt").read_bytes() == b"keep local work"


@pytest.mark.parametrize("incremental", [False, True], ids=["initial", "incremental"])
@pytest.mark.parametrize("same_candidate", [True, False], ids=["retry", "concurrent-writer"])
def test_overlapping_attempts_have_one_publication(publisher, incremental, same_candidate):
    _, auth, _, service, git, first, prepare = publisher
    old = first if incremental else None
    if incremental:
        request(service, first, prepare)
    candidate = git.commit({"retry": b"candidate"})
    other = candidate if same_candidate else git.commit({"other": b"concurrent candidate"})
    prepare()
    both_prepared = Barrier(2)

    def publish(oid):
        return request(service, oid, lambda: both_prepared.wait(timeout=10), old=old)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(publish, [candidate, other]))
    assert sorted(result["status"] for result in results) == ["committed", "rejected"]
    winner = next(result for result in results if result["status"] == "committed")
    assert auth.state()["oid"] == winner["refs"][0]["new"]["oid"]
    assert service.control.snapshot(auth.project)["ref_sequence"] == (2 if incremental else 1)


def test_cancelled_execution_cannot_publish_after_upload_finishes(publisher):
    _, auth, _, service, _, oid, prepare = publisher
    execution = Execution()

    def upload_then_disconnect():
        prepare()
        execution.cancelled.set()

    token = current_execution.set(execution)
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            request(service, oid, upload_then_disconnect)
    finally:
        current_execution.reset(token)
    assert auth.state() is None
    assert request(service, oid, prepare)["status"] == "committed"


@pytest.mark.parametrize("incremental", [False, True], ids=["initial", "incremental"])
def test_lost_response_followed_by_fresh_attempt_does_not_publish_twice(publisher, incremental):
    _, auth, _, service, git, first, prepare = publisher
    old = first if incremental else None
    if incremental:
        request(service, first, prepare)
    oid = git.commit({"retry": b"same captured commit"})
    apply = service.control.apply
    key = str(uuid.uuid4())

    def commit_without_response(*args):
        apply(*args)
        raise ConnectionError("response lost after commit")

    service.control.apply = commit_without_response
    with pytest.raises(ConnectionError):
        request(service, oid, prepare, key=key, old=old)
    service.control.apply = apply
    retry = request(service, oid, prepare, old=old)
    assert retry["status"] == "rejected"
    assert auth.state()["oid"] == oid
    receipt = service.control.result(auth.project, "user:fixture-user", key)
    assert receipt["status"] == "committed"
    assert service.control.snapshot(auth.project)["ref_sequence"] == (2 if incremental else 1)
