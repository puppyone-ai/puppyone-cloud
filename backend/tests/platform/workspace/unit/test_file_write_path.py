"""A live workspace save waits for admission and its intent, not for bookkeeping.

Desktop writes a Cloud document after each typing pause. Every database round
trip on that path is a pause the person sees. Admission (the SQL revision fence
and the runtime billing check) and the intent audit run together; the intent
still lands before the sandbox write. Completion audits run after the reply.
Reads keep their final authorization because they return file bytes.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.filesystem.port import FilesystemError
from src.platform.workspace.lifecycle import meter_id
from src.platform.workspace.service import WorkbenchService

USER, PROJECT, GENERATION = "user-1", "project-1", "generation-1"
ROW = {
    "id": "workbench-1",
    "generation": GENERATION,
    "state": "running",
    "compute_epoch": 1,
    "user_id": USER,
    "project_id": PROJECT,
}
WRITE = {"action": "write", "path": "notes.md", "content_base64": "aGk=", "expected_revision": None}
READ = {"action": "read", "path": "notes.md"}
DELAY = 0.2


class Repository:
    def __init__(self, journal):
        self.journal = journal
        self.audits = []
        self.activity_error = None
        self.compute_epoch = 1

    def activity(self, row, revision, *, write=False):
        self.journal.append("activity:start")
        time.sleep(DELAY)
        self.journal.append("activity:end")
        if self.activity_error:
            raise self.activity_error
        return {**row, "compute_epoch": self.compute_epoch}

    def audit(self, user, project, generation, operation, identity, status):
        self.journal.append(f"audit:{status}:start")
        time.sleep(DELAY)
        self.audits.append((status, identity, operation["path"]))
        self.journal.append(f"audit:{status}:end")


class Billing:
    def __init__(self, journal):
        self.journal = journal
        self.expired = set()
        self.checked = []

    async def check_session(self, run_id):
        self.journal.append("billing:start")
        await asyncio.sleep(DELAY)
        self.checked.append(run_id)
        self.journal.append("billing:end")
        if run_id in self.expired:
            raise ValueError("Runtime reservation expired or was exhausted")


class Authorization:
    def __init__(self):
        self.denied = False

    def authorize_facts(self, facts, project, user, action):
        if self.denied:
            raise PermissionError("not a member")
        return SimpleNamespace(project=project, user=user, action=action)


class Filesystem:
    def __init__(self, journal, repository):
        self.journal, self.repository = journal, repository
        self.failure = None
        self.audits_seen = None

    async def operation(self, channel, operation):
        self.audits_seen = [status for status, *_ in self.repository.audits]
        self.journal.append("operation")
        if self.failure:
            raise self.failure
        return (
            {"revision": "r" * 64} if operation["action"] == "write" else {"content_base64": "aGk="}
        )


class Channels:
    def __init__(self, journal):
        self.journal = journal

    @asynccontextmanager
    async def borrow(self, row):
        self.journal.append("borrow")
        yield SimpleNamespace(admitted_row=row)

    async def close(self):
        pass


@pytest.fixture
def workspace():
    journal = []
    repository = Repository(journal)
    billing = Billing(journal)
    authorization = Authorization()
    service = WorkbenchService(
        SimpleNamespace(),
        Filesystem(journal, repository),
        billing,
        repository=repository,
        authorization=authorization,
        channel_factory=lambda *args, **kwargs: None,
        providers=lambda name=None: None,
    )
    service.channels = Channels(journal)
    service.filesystem = Filesystem(journal, repository)

    async def bound(*args, **kwargs):
        return await _bound(journal)

    service.bound = AsyncMock(side_effect=bound)
    service.snapshots.put(USER, PROJECT, {"workbench": dict(ROW), "facts": {}, "revision": 7})
    return SimpleNamespace(
        service=service,
        journal=journal,
        repository=repository,
        billing=billing,
        authorization=authorization,
    )


async def _bound(journal):
    journal.append("bound")
    return dict(ROW), {"workbench": dict(ROW), "facts": {}, "revision": 7}


async def test_warm_save_overlaps_admission_and_intent_then_replies_before_completion(workspace):
    started = time.monotonic()
    result = await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    elapsed = time.monotonic() - started

    assert result == {"revision": "r" * 64}
    # Fence, billing and intent ran together: about one delay, not three.
    assert elapsed < DELAY * 2.5
    journal = workspace.journal
    assert journal.index("operation") > max(
        journal.index("activity:end"),
        journal.index("billing:end"),
        journal.index("audit:intent:end"),
    )
    assert workspace.service.filesystem.audits_seen == ["intent"]
    # The reply did not wait for the completion audit, nor re-authorize.
    assert [status for status, *_ in workspace.repository.audits] == ["intent"]
    assert "bound" not in journal
    await workspace.service.settle_audits()
    audits = workspace.repository.audits
    assert [status for status, *_ in audits] == ["intent", "completed"]
    assert audits[0][1] == audits[1][1]


@pytest.mark.parametrize("action", ["mkdir", "move", "remove"])
async def test_every_mutation_receipt_skips_final_authorization(workspace, action):
    operation = {"action": action, "path": "folder"}
    if action == "move":
        operation["destination"] = "renamed"
    await workspace.service.file_operation(USER, PROJECT, GENERATION, operation)
    await workspace.service.settle_audits()
    assert "bound" not in workspace.journal
    assert [status for status, *_ in workspace.repository.audits] == ["intent", "completed"]


async def test_read_keeps_final_authorization_and_writes_no_audit(workspace):
    result = await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(READ))
    assert result == {"content_base64": "aGk="}
    assert workspace.journal.index("bound") > workspace.journal.index("operation")
    await workspace.service.settle_audits()
    assert workspace.repository.audits == []


async def test_fence_refusal_closes_an_early_intent_without_touching_files(workspace):
    workspace.repository.activity_error = WorkspaceError(409, {"code": "workbench_not_running"})
    with pytest.raises(WorkspaceError):
        await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    await workspace.service.settle_audits()
    assert "operation" not in workspace.journal and "borrow" not in workspace.journal
    statuses = [status for status, *_ in workspace.repository.audits]
    assert statuses == ["intent", "rejected"]


async def test_moved_authorization_revision_reloads_facts_without_a_second_intent(workspace):
    moved = Exception("P0001: agent_context_changed")
    moved.code = "P0001"
    workspace.repository.activity_error = moved

    async def fresh(*args, **kwargs):
        workspace.repository.activity_error = None
        return await _bound(workspace.journal)

    workspace.service.bound = AsyncMock(side_effect=fresh)
    await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    await workspace.service.settle_audits()
    assert workspace.journal.count("audit:intent:start") == 1
    assert [status for status, *_ in workspace.repository.audits] == ["intent", "completed"]


async def test_a_caller_the_policy_refuses_leaves_no_audit_row(workspace):
    workspace.authorization.denied = True

    async def refused(*args, **kwargs):
        workspace.journal.append("bound")
        raise PermissionError("not a member")

    workspace.service.bound = AsyncMock(side_effect=refused)
    with pytest.raises(PermissionError):
        await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    await workspace.service.settle_audits()
    assert workspace.repository.audits == []
    assert "operation" not in workspace.journal


async def test_cold_admission_authorizes_before_the_intent(workspace):
    workspace.service.snapshots.discard(USER, PROJECT)
    await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    journal = workspace.journal
    assert journal.index("bound") < journal.index("audit:intent:start") < journal.index("operation")


async def test_a_refused_sandbox_write_is_recorded_as_rejected(workspace):
    workspace.service.filesystem.failure = FilesystemError("file_conflict")
    with pytest.raises(WorkspaceError) as refused:
        await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    assert refused.value.status_code == 409
    await workspace.service.settle_audits()
    assert [status for status, *_ in workspace.repository.audits] == ["intent", "rejected"]


async def test_lost_transport_leaves_the_intent_as_an_unknown_outcome(workspace):
    workspace.service.filesystem.failure = ConnectionResetError("peer reset")
    with pytest.raises(ConnectionResetError):
        await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    await workspace.service.settle_audits()
    assert [status for status, *_ in workspace.repository.audits] == ["intent"]


async def test_billing_rechecks_the_current_compute_after_a_stale_snapshot(workspace):
    workspace.repository.compute_epoch = 2
    workspace.billing.expired.add(meter_id(ROW))
    await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    assert workspace.billing.checked == [meter_id(ROW), meter_id({**ROW, "compute_epoch": 2})]


async def test_an_expired_reservation_fails_closed(workspace):
    workspace.billing.expired.add(meter_id(ROW))
    with pytest.raises(ValueError):
        await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    await workspace.service.settle_audits()
    assert "operation" not in workspace.journal
    assert [status for status, *_ in workspace.repository.audits] == ["intent", "rejected"]


async def test_close_waits_for_pending_audits(workspace):
    await workspace.service.file_operation(USER, PROJECT, GENERATION, dict(WRITE))
    assert workspace.service.audits
    await workspace.service.close()
    assert [status for status, *_ in workspace.repository.audits] == ["intent", "completed"]
