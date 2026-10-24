"""Desktop's optional HTTP attempt ID binds exactly one initial main edit."""

import io
import uuid
from random import Random

import pytest

from src.platform.access.adapters.git.native_repository import NativeGitRepository
from src.platform.access.adapters.git.native_wire import ReceivePush, receive_request
from src.platform.access.adapters.git.protocol import pkt_line
from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.integration.test_ref_transaction_service import grant
from tests.repository_hosting.unit.test_direct_object_transport import storage

pytestmark = pytest.mark.hosting_component


@pytest.mark.parametrize("attempt", [None, "41f38842-ef41-41d6-a259-0bd22690aa61", "opaque-probe-metadata"])
def test_probe_does_not_open_storage_or_consume_publication_identity(tmp_path, attempt):
    git = Git.init(tmp_path / "source")
    git.commit({"file": b"contents"})
    service, reads, pins, _ = storage(git)

    def forbidden(*args, **kwargs):
        pytest.fail("Transport probe entered the publication/storage path")

    service.control.begin_read = forbidden
    service.submit = forbidden
    service.backend.put_git_pack_durable = forbidden
    path = tmp_path / "probe"
    path.write_bytes(b"0000")
    response = NativeGitRepository(service).receive(grant("p"), path, initial_attempt=attempt)
    assert response.status_code == 200 and response.body == b""
    assert response.media_type == "application/x-git-receive-pack-result"
    assert not reads and not pins


def test_probe_still_requires_write_authority_and_cannot_ack_a_publication(tmp_path):
    git = Git.init(tmp_path / "source")
    git.commit({"file": b"contents"})
    service, reads, pins, _ = storage(git)
    path = tmp_path / "probe"
    path.write_bytes(b"0000")
    repository = NativeGitRepository(service)
    with pytest.raises(PermissionError):
        repository.receive(grant("p", writable=False), path)
    with pytest.raises(ValueError, match="Publication requires ref updates"):
        repository.receive(grant("p"), path, publication=object())
    assert not reads and not pins


@pytest.mark.parametrize("body", [
    b"", b"000", b"0001", b"0000PACK", b"00000000",
    pkt_line(b"shallow " + b"1" * 40 + b"\n") + b"0000",
])
def test_malformed_empty_requests_are_not_probes(body):
    with pytest.raises(ValueError):
        receive_request(io.BytesIO(body), "sha1")


def test_delete_without_pack_is_a_real_push():
    body = pkt_line(b"1" * 40 + b" " + b"0" * 40 + b" refs/heads/main\0report-status\n") + b"0000"
    request = receive_request(io.BytesIO(body), "sha1")
    assert isinstance(request, ReceivePush)
    assert len(request.edits) == 1 and request.edits[0].new.oid is None


@pytest.mark.parametrize("format", ["sha1", "sha256"])
@pytest.mark.parametrize("known_attempt", [True, False])
def test_initial_pack_binds_known_attempt_and_retains_stock_git_path(tmp_path, format, known_attempt):
    git = Git.init(tmp_path / "source", format=format)
    oid = git.commit({"file": b"contents"})
    service, _, pins, _ = storage(git)
    begin = service.control.begin_read

    def empty(*args):
        snapshot = begin(*args)
        snapshot["refs"] = [row for row in snapshot["refs"] if row["name_b64"] == "SEVBRA=="]
        return snapshot

    service.control.begin_read = empty
    received = []

    def submit(_grant, **kwargs):
        received.append(kwargs)
        return {"status": "committed"}

    service.submit = submit
    pack = git.run("pack-objects", "--stdout", "--revs", input=(oid + "\n").encode()).stdout
    path = tmp_path / "request"
    path.write_bytes(pkt_line(f"{'0' * len(oid)} {oid} refs/heads/main\0report-status\n".encode()) + b"0000" + pack)
    attempt = str(uuid.uuid4()) if known_attempt else None
    result = NativeGitRepository(service).receive(grant("p"), path, initial_attempt=attempt)
    assert len(received) == 1 and not pins
    assert str(uuid.UUID(received[0]["request_key"])) == (attempt or received[0]["request_key"])
    assert received[0]["edits"][0].new.oid == oid
    assert b"ok refs/heads/main" in result.body


@pytest.mark.parametrize("fault", ["invalid-id", "different-ref", "update", "delete", "multiple"])
def test_attempt_extension_rejects_non_initial_input_before_pack_or_snapshot(tmp_path, fault):
    git = Git.init(tmp_path / "source")
    oid = git.commit({"file": b"contents"})
    service, reads, pins, _ = storage(git)
    zero = "0" * 40
    old = oid if fault == "update" else zero
    new = zero if fault == "delete" else oid
    ref = "refs/heads/other" if fault == "different-ref" else "refs/heads/main"
    data = pkt_line(f"{old} {new} {ref}\0report-status\n".encode())
    if fault == "multiple":
        data += pkt_line(f"{zero} {oid} refs/heads/other\n".encode())
    path = tmp_path / "request"
    path.write_bytes(data + b"0000")
    with pytest.raises(ValueError):
        NativeGitRepository(service).receive(grant("p"), path, initial_attempt="bad" if fault == "invalid-id" else str(uuid.uuid4()))
    assert not pins and not reads


@pytest.mark.parametrize("large", [False, True], ids=["buffered", "chunked-with-probe"])
def test_stock_git_http_header_reaches_native_transaction_key(tmp_path, monkeypatch, large):
    from fastapi import FastAPI, Request

    from src.platform.access.adapters.git import router
    from src.platform.access.adapters.git.native import NativeGitEndpoint
    from tests.repository_hosting.harness.http_server import _serve_git_app

    git = Git.init(tmp_path / "source")
    content = Random(7).randbytes(1536 * 1024) if large else b"contents"
    oid = git.commit({"file": content})
    service, _, pins, _ = storage(git)
    snapshot = service.control.read_snapshot()
    snapshot["refs"] = [row for row in snapshot["refs"] if row["name_b64"] == "SEVBRA=="]
    service.control.read_snapshot = lambda *_: snapshot

    def begin(_project, _actor, pin):
        pins.add(pin)
        return snapshot | {"pin_id": pin}

    service.control.begin_read = begin
    received = []

    def submit(_grant, **kwargs):
        received.append(kwargs["request_key"])
        return {"status": "committed"}

    service.submit = submit
    endpoint = NativeGitEndpoint(service, grant("p"), None)
    app = FastAPI()
    requests = []
    monkeypatch.setattr(router, "_git_receive_max_body_bytes", lambda _: 128 * 1024**2)

    @app.get("/repo.git/info/refs")
    def info(service: str):
        return endpoint.info_refs(service)

    @app.post("/repo.git/git-receive-pack")
    async def receive(request: Request):
        body = await request.body()
        requests.append((len(body), body[:4], request.headers.get("transfer-encoding")))
        return await router._native_rpc(endpoint, request, upload=False, project_id="p")

    attempt = str(uuid.uuid4())
    with _serve_git_app(app) as address:
        remote = address + "/repo.git"
        git.run("-c", f"http.{remote}.extraHeader=X-PuppyOne-Initial-Attempt: {attempt}",
                "push", "--force-with-lease=refs/heads/main:", remote, f"{oid}:refs/heads/main")
    assert received == [attempt] and not pins
    if large:
        assert len(requests) == 2
        assert requests[0][:2] == (4, b"0000")
        assert requests[1][0] > 1024**2 and requests[1][2] == "chunked"
    else:
        assert len(requests) == 1
