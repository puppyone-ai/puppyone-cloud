"""Stock Git through the Agent relay, real PG refs and durable disk objects."""

import base64
from random import Random
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request, Response

from src.platform.access.adapters.git.workspace_transport import RunGitTransport, RunPublication
from src.platform.access.adapters.git.native_repository import NativeGitRepository
from src.platform.access.adapters.git.protocol import pkt_line
from src.version_engine.read.ref_metadata import GitBranchBase
from tests.repository_hosting.harness.http_server import _serve_git_app
from tests.repository_hosting.integration.test_ref_transaction_service import grant, request
from tests.repository_hosting.integration.test_ref_transaction_service import publisher as publisher_fixture

pytestmark = pytest.mark.hosting_live
publisher = publisher_fixture


@pytest.mark.parametrize("incremental", [False, True], ids=["initial", "incremental"])
def test_agent_chunked_probe_does_not_consume_original_run(publisher, incremental):
    _, auth, backend, service, git, first, prepare = publisher
    if incremental:
        assert request(service, first, prepare)["status"] == "committed"
    candidate = git.commit({"large": Random(7).randbytes(128 * 1024)})
    (git.path / "unsaved.txt").write_text("newer human work")
    base = GitBranchBase.from_metadata(service.control.snapshot(auth.project), target_ref="refs/heads/main")
    binding = RunPublication(str(uuid4()), base, candidate)
    transport = RunGitTransport(SimpleNamespace(service_from_metadata=lambda *_: service))
    transactions_before = auth.count("version_ref_transactions")
    durable_before = set(backend.all_hashes())
    transfers = []
    app = FastAPI()

    @app.api_route("/git/{project}.git/{route:path}", methods=["GET", "POST"])
    async def relay(http: Request):
        body = await http.body()
        path = http.url.path + ("?" + http.url.query if http.url.query else "")
        result = await transport.exchange(
            auth.project, grant(auth.project),
            {"method": http.method, "path": path, "body": base64.b64encode(body).decode()},
            base=base.wire(), publication=binding,
        )
        decoded = base64.b64decode(result["body"])
        if http.method == "POST":
            probe = body == b"0000"
            transfers.append((probe, http.headers.get("transfer-encoding")))
            if probe:
                assert result["status"] == 200 and decoded == b""
                assert auth.count("version_ref_transactions") == transactions_before
                assert set(backend.all_hashes()) == durable_before
                assert service.control.result(auth.project, "user:fixture-user", binding.request_key) is None
        return Response(decoded, status_code=result["status"], media_type=result["content_type"])

    with _serve_git_app(app) as address:
        git.run("-c", "http.postBuffer=65536", "push", address + f"/git/{auth.project}.git",
                f"{candidate}:refs/heads/main")
    assert transfers == [(True, None), (False, "chunked")]
    assert auth.count("version_ref_transactions") == transactions_before + 1
    assert auth.state()["oid"] == candidate
    assert service.control.result(auth.project, "user:fixture-user", binding.request_key)["status"] == "committed"
    assert git.text("rev-parse", "HEAD") == candidate
    assert (git.path / "unsaved.txt").read_text() == "newer human work"


def test_run_host_rejects_non_descendant_even_if_client_bypasses_fast_forward_check(publisher, tmp_path):
    _, auth, _, service, git, first, prepare = publisher
    assert request(service, first, prepare)["status"] == "committed"
    winner = git.commit({"file": b"remote winner"})
    assert request(service, winner, prepare, old=first)["status"] == "committed"
    git.run("reset", "--hard", first)  # Only this disposable test checkout.
    candidate = git.commit({"file": b"retained local work"})
    base = GitBranchBase.from_metadata(service.control.snapshot(auth.project), target_ref="refs/heads/main")
    binding = RunPublication(str(uuid4()), base, candidate)
    pack = git.run("pack-objects", "--stdout", "--revs", input=(candidate + "\n").encode()).stdout
    body = tmp_path / "receive-pack"
    body.write_bytes(pkt_line(f"{winner} {candidate} refs/heads/main\0report-status\n".encode()) + b"0000" + pack)
    transactions_before = auth.count("version_ref_transactions")
    result = NativeGitRepository(service).receive(grant(auth.project), body, publication=binding)
    assert b"ng refs/heads/main Run candidate does not descend from its base" in result.body
    assert auth.state()["oid"] == winner
    assert auth.count("version_ref_transactions") == transactions_before
    assert git.text("rev-parse", "HEAD") == candidate
    assert (git.path / "file").read_bytes() == b"retained local work"
