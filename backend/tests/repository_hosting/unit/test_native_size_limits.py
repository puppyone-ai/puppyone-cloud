"""Native decoded bytes are bounded independently of wire bytes and RAM."""

import io
import random
from types import SimpleNamespace

import pytest
from pydantic import ValidationError, create_model

from src.config import Settings
from src.platform.access.adapters.git import object_pack
from src.version_engine.domain.git_pack_limits import MAX_RECEIVE_PACK_BYTES
from src.version_engine.storage.git_pack import GitPack
from src.version_engine.storage.git_pack_cache import pack_cache
from src.version_engine.storage.git_pack_limits import PackLimits
from src.version_engine.storage.object_store import FileSystemBackend
from src.version_engine.write_engine.git_object_format import decode_object, hash_object
from tests.repository_hosting.harness.git import Git

pytestmark = pytest.mark.hosting_component


def test_compressed_repository_does_not_expand_to_a_cumulative_spool(tmp_path):
    git = Git.init(tmp_path / "source")
    oid = git.commit({f"file-{i}": bytes([65 + i]) * 4096 for i in range(4)})
    payload = git.run("pack-objects", "--stdout", "--revs", input=(oid + "\n").encode()).stdout
    assert len(payload) < 4096
    reader = SimpleNamespace(format="sha1", snapshot=SimpleNamespace(check_live=lambda: None))
    with object_pack.IncomingPack(reader) as incoming:
        directory = incoming.directory
        incoming.read(io.BytesIO(payload))
        assert incoming.path.read_bytes() == payload
        assert {key: incoming.get(key) for key in incoming.objects} == git.objects()
        assert sum(row[2] for row in incoming.objects.values()) > 8192
        assert not list((directory / "objects").glob("??/*"))
    assert not directory.exists()


@pytest.mark.parametrize("value", [0, -1, 8 * 1024**3 + 1])
def test_native_expansion_budget_cannot_be_disabled_or_unbounded(value):
    # Validate the actual Settings field without loading developer secrets or
    # unrelated deployment validators.
    budget = create_model("NativeBudget", limit=(int, Settings.model_fields["GIT_MAX_EXPANDED_BYTES"]))
    with pytest.raises(ValidationError):
        budget(limit=value)


def test_publication_factory_uses_the_same_logical_work_budget_as_reads():
    from src.config import settings
    from src.platform.access.adapters.git.execution import MAX_NATIVE_GRAPH_BYTES
    from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager

    service = VersionRepoManager(object(), SimpleNamespace(client=object())).service_from_metadata(
        "proj", {"project_id": "proj", "object_format": "sha1"}
    )
    assert service.verifier.max_bytes == MAX_NATIVE_GRAPH_BYTES == settings.GIT_MAX_EXPANDED_BYTES
    assert Settings.model_fields["GIT_MAX_EXPANDED_BYTES"].default == 8 * 1024**3


@pytest.mark.parametrize("field", ["GIT_MAX_RECEIVE_PACK_BYTES", "GIT_MAX_STORED_PACK_BYTES", "GIT_PACK_SCRATCH_BYTES"])
@pytest.mark.parametrize("value", [0, -1])
def test_pack_configuration_cannot_disable_a_resource_bound(field, value):
    model = create_model("PackBudget", limit=(int, Settings.model_fields[field]))
    with pytest.raises(ValidationError):
        model(limit=value)


@pytest.mark.parametrize("values", [
    {"receive_bytes": 3 * 1024**3},
    {"scratch_bytes": 1024**3},
    {"stored_bytes": 2**63},
])
def test_inconsistent_pack_resource_profiles_fail_at_startup(values):
    with pytest.raises(ValueError):
        PackLimits(**values)


def test_settings_reject_inconsistent_pack_profile():
    with pytest.raises(ValidationError, match="scratch"):
        Settings(_env_file=None, APP_ENV="test", GIT_PACK_SCRATCH_BYTES=1024**3)


def test_receive_policy_accepts_two_gib_but_cannot_be_raised_one_byte():
    field = Settings.model_fields["GIT_MAX_RECEIVE_PACK_BYTES"]
    model = create_model("ReceivePolicy", limit=(int, field))
    assert MAX_RECEIVE_PACK_BYTES == 2_147_483_648
    assert model().limit == PackLimits().receive_bytes == MAX_RECEIVE_PACK_BYTES
    assert model(limit=MAX_RECEIVE_PACK_BYTES).limit == MAX_RECEIVE_PACK_BYTES
    with pytest.raises(ValidationError):
        model(limit=MAX_RECEIVE_PACK_BYTES + 1)
    with pytest.raises(ValueError, match="2 GiB"):
        PackLimits(receive_bytes=MAX_RECEIVE_PACK_BYTES + 1)
    assert PackLimits().stored_bytes > MAX_RECEIVE_PACK_BYTES


def test_exact_wire_limit_is_inclusive_and_rejection_leaves_no_scratch(tmp_path):
    source = Git.init(tmp_path / "boundary-source")
    source.commit({"file": b"a small real pack exercises the same byte boundary"})
    payload = source.run("pack-objects", "--stdout", "--all").stdout
    with GitPack("sha1", limits=PackLimits(receive_bytes=len(payload))) as pack:
        pack.read(io.BytesIO(payload))
        assert pack.path.read_bytes() == payload
    with GitPack("sha1", limits=PackLimits(receive_bytes=len(payload) - 1)) as pack:
        directory = pack.directory
        with pytest.raises(ValueError, match="byte limit"):
            pack.read(io.BytesIO(payload))
    assert not directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_attempt", [None, "11111111-1111-4111-8111-111111111111"])
async def test_two_gib_http_limit_rejects_declared_overflow_before_reading(monkeypatch, initial_attempt):
    from fastapi import HTTPException

    from src.platform.access.adapters.git import router
    from src.version_engine.domain.git_pack_limits import MAX_RECEIVE_COMMAND_BYTES

    monkeypatch.setattr(router, "_git_receive_max_body_bytes", lambda _: MAX_RECEIVE_PACK_BYTES)

    class Request:
        def __init__(self):
            self.headers = {"content-length": str(MAX_RECEIVE_PACK_BYTES + MAX_RECEIVE_COMMAND_BYTES + 1)}
            if initial_attempt:
                self.headers["x-puppyone-initial-attempt"] = initial_attempt

        async def stream(self):
            pytest.fail("an oversized Content-Length must be rejected before reading")
            yield b""

    with pytest.raises(HTTPException) as failure:
        await router._native_rpc(SimpleNamespace(), Request(), upload=False, project_id="project")
    assert failure.value.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_attempt", [None, "11111111-1111-4111-8111-111111111111"])
async def test_router_preserves_pack_allowance_and_forwards_plan_cap(monkeypatch, initial_attempt):
    from fastapi.responses import Response

    from src.platform.access.adapters.git import router

    body, seen = b"commands\0PACK", []
    monkeypatch.setattr(router, "_git_receive_max_body_bytes", lambda _: 4)

    class Request:
        def __init__(self):
            self.headers = {"content-length": str(len(body))}
            if initial_attempt:
                self.headers["x-puppyone-initial-attempt"] = initial_attempt

        async def stream(self):
            yield body

        async def is_disconnected(self):
            return False

    def receive(path, *, max_pack_bytes, **kwargs):
        seen.append((max_pack_bytes, kwargs.get("initial_attempt")))
        assert path.read_bytes() == body
        return Response("accepted")

    result = await router._native_rpc(
        SimpleNamespace(receive=receive), Request(), upload=False, project_id="project"
    )
    assert result.status_code == 200 and seen == [(4, initial_attempt)]


def test_protocol_command_budget_cannot_be_used_for_pack_bytes(tmp_path):
    from src.platform.access.adapters.git.native_repository import NativeGitRepository
    from src.platform.access.adapters.git.protocol import pkt_line
    from tests.repository_hosting.integration.test_ref_transaction_service import grant
    from tests.repository_hosting.unit.test_direct_object_transport import storage

    source = Git.init(tmp_path / "source")
    old = source.commit({"file": b"old"})
    service, _, pins, _ = storage(source)
    new = source.commit({"file": b"new"})
    payload = source.run("pack-objects", "--stdout", "--all").stdout
    request = tmp_path / "request"
    request.write_bytes(pkt_line(f"{old} {new} refs/heads/main\0report-status\n".encode())
                        + b"0000" + payload)
    response = NativeGitRepository(service).receive(grant("p"), request, max_pack_bytes=len(payload) - 1)
    assert b"unpack invalid pack:" in response.body and b"byte limit" in response.body
    assert not pins
    # The fake service has no publisher: reaching publication would fail this test.


def test_lower_receive_policy_does_not_break_s3_cold_reads(tmp_path, monkeypatch):
    from src.config import settings
    from src.version_engine.storage.backends.s3 import S3StorageBackend
    from src.version_engine.storage.mutation_context import publication_storage
    from tests.repository_hosting.unit.test_standard_git_pack import StreamS3
    from tests.repository_hosting.unit.test_streamed_storage import FencedDB

    source = Git.init(tmp_path / "source")
    head = source.commit({"file": b"read after policy change"})
    payload = source.run("pack-objects", "--stdout", "--all").stdout
    database = FencedDB()
    s3 = StreamS3(database)
    backend = S3StorageBackend(s3, "proj", supabase=database)
    try:
        with GitPack("sha1") as incoming, publication_storage("proj", "actor", "pin", require_capacity=True):
            incoming.read(io.BytesIO(payload))
            backend.put_git_pack_durable(incoming)
        pack_cache.clear()
        monkeypatch.setattr(settings, "GIT_MAX_RECEIVE_PACK_BYTES", 1)
        with GitPack("sha1") as rejected, pytest.raises(ValueError, match="byte limit"):
            rejected.read(io.BytesIO(payload))
        cold = S3StorageBackend(s3, "proj", supabase=database)
        kind, body = decode_object(cold.get(head))
        assert kind == "commit" and hash_object(kind, body) == head
    finally:
        pack_cache.clear()


def test_real_pack_above_former_128_mib_cap_survives_cold_read(tmp_path, monkeypatch):
    """Exercise real incompressible bytes, not a tiny pack with a mocked stat."""
    from src.config import settings

    source = Git.init(tmp_path / "large-source", bare=True)
    entries, oids = [], []
    random_bytes = random.Random(7421)
    blob = tmp_path / "blob"
    for number in range(3):
        with blob.open("wb") as output:
            for _ in range(44):
                output.write(random_bytes.randbytes(1024**2))
        oid = source.text("hash-object", "-w", str(blob))
        oids.append(oid)
        entries.append(f"100644 blob {oid}\tfile-{number}\n")
    blob.unlink()
    tree = source.run("mktree", input="".join(entries).encode()).stdout.strip()
    head = source.run("commit-tree", tree.decode(), "-m", "large pack").stdout.strip()
    prefix = tmp_path / "generated"
    digest = source.run("pack-objects", "--revs", "--window=0", "--compression=1", str(prefix),
                        input=head + b"\n").stdout.decode().strip()
    path = tmp_path / f"generated-{digest}.pack"
    assert path.stat().st_size > 128 * 1024**2
    backend = FileSystemBackend(tmp_path / "durable")
    try:
        with GitPack("sha1") as pack, path.open("rb") as stream:
            pack.read(stream)
            assert pack.path.stat().st_size == path.stat().st_size
            backend.put_git_pack_durable(pack)
        path.unlink()  # Original receive bytes are gone; only durable storage remains.
        pack_cache.clear()
        monkeypatch.setattr(settings, "GIT_MAX_RECEIVE_PACK_BYTES", 1024)
        for oid in oids:
            kind, body = decode_object(backend.get(oid))
            assert kind == "blob" and len(body) == 44 * 1024**2
            assert hash_object(kind, body) == oid
    finally:
        pack_cache.clear()
