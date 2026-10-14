"""Actual owned PostgreSQL/PostgREST/S3 ref publication; no compute provider."""
import asyncio
from uuid import uuid4

import pytest

from src.platform.project.write_lease import ProjectWriteLease
from src.version_engine.read.ref_metadata import GitBranchBase, decode_ref_name
from src.version_engine.write_engine.ref_transaction import RefEdit, RefState
from tests.agent.runtime.test_supervisor import native_write
from tests.agent.runtime.test_supervisor import prepared as prepared_fixture

prepared = prepared_fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("prepared", ["native", "native_sha256"], indirect=True)
async def test_explicit_ref_cas_receipt_and_display_following(prepared):
    case = prepared
    await native_write(case, "note", b"original display")
    grant = case.admission.authorization.resolve_project_grant(case.project, case.user)
    transport = case.publication.transport
    manager = transport.manager
    service = manager.get_native_service(case.project)
    displayed = transport.describe(case.project, grant)
    candidate = displayed["expected_oid"]
    base = GitBranchBase.parse(transport.describe(case.project, grant, target_ref="refs/heads/workbench"))
    assert base.head_guard is None and base.expected.oid is None

    async def submit(edits, roots, key=None):
        async with ProjectWriteLease(case.project, "workbench-ref-acceptance"):
            return await asyncio.to_thread(service.submit, grant, request_key=key or str(uuid4()),
                generation=base.generation, edits=edits, roots=roots,
                prepare=lambda: None, message="Workbench ref acceptance")

    # Move the display source AFTER capture, through the same native authority.
    assert (await submit([RefEdit(b"refs/heads/display", RefState(), RefState(oid=candidate))],
                         {candidate: "commit"}))["status"] == "committed"
    assert (await submit([RefEdit(b"HEAD", RefState(target=b"refs/heads/main"),
                                 RefState(target=b"refs/heads/display"))], {}))["status"] == "committed"
    key = str(uuid4())
    result = await submit(base.edits(candidate), {candidate: "commit"}, key)
    assert result["status"] == "committed"
    assert transport.result(case.project, grant, key) == result
    assert await submit(base.edits(candidate), {candidate: "commit"}, key) == result

    def refs():
        return {decode_ref_name(row["name_b64"]): row["state"]
                for row in manager.get_native_ref_metadata(case.project, grant)["refs"]}

    current = refs()
    assert current[b"HEAD"] == RefState(target=b"refs/heads/display").wire(base.object_format)
    assert current[b"refs/heads/main"] == current[b"refs/heads/workbench"] == RefState(oid=candidate).wire(base.object_format)
    # Different operation, same stale absent-ref expectation: native CAS rejects.
    assert (await submit(base.edits(candidate), {candidate: "commit"}))["status"] == "rejected"

    # Following is explicit native HEAD selection, never another display pointer.
    assert (await submit([RefEdit(b"HEAD", RefState(target=b"refs/heads/display"),
                                 RefState(target=b"refs/heads/workbench"))], {}))["status"] == "committed"
    await native_write(case, "note", b"selected workbench saved")
    current = refs()
    assert current[b"refs/heads/main"]["oid"] == candidate
    assert current[b"refs/heads/display"]["oid"] == candidate
    assert current[b"refs/heads/workbench"]["oid"] != candidate
    with case.ops.open_read(case.project, grant) as reader:
        assert reader.read_file(case.project, "note") == b"selected workbench saved"
