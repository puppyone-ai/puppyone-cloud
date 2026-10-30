"""Workspace Git outside a Run: the branch handoffs every collaboration depends on."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.sync import WorkspaceGit
from src.version_engine.read.ref_metadata import GitBranchBase, RefState

WORKSPACE_REF = "refs/heads/workspaces/owner"
MAIN = "refs/heads/main"


def base(ref, oid):
    guard = RefState(target=ref.encode()) if ref == MAIN else None
    return GitBranchBase("sha1", 1, ref.encode(), RefState(oid=oid), guard).wire()


ROW = {
    "id": "w",
    "user_id": "owner",
    "project_id": "project",
    "target_ref": WORKSPACE_REF,
    "generation": "g",
}


class Channel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self.git_handler = None

    async def control(self, action, value=None):
        if action == "capabilities":
            return {"workspace_git": 1}
        self.calls.append(value)
        return self.replies.pop(0)


def workspace(replies, *, refs=None, result=None, active=False):
    refs = {WORKSPACE_REF: "a" * 40, MAIN: "b" * 40, **(refs or {})}

    def describe(project, grant, target_ref=None):
        ref = target_ref or MAIN
        return base(ref, refs[ref])

    channel = Channel(replies)
    transport = SimpleNamespace(
        describe=Mock(side_effect=describe),
        result=Mock(return_value=result or {"status": "committed"}),
        exchange=AsyncMock(return_value={"status": 200}),
        publication=lambda key, selected, candidate: SimpleNamespace(request_key=key),
    )
    service = SimpleNamespace(
        transport=transport,
        context=Mock(return_value=({}, "grant")),
        repository=SimpleNamespace(has_active_runs=Mock(return_value=active)),
        channel_factory=Mock(return_value=channel),
        connection=AsyncMock(return_value="connection"),
    )
    return WorkspaceGit(service), channel, service


@pytest.mark.asyncio
async def test_sync_defaults_to_the_projects_branch():
    git, channel, _ = workspace([{"status": "merged", "tip": "c" * 40}])
    result = await git.sync(ROW)
    assert result["status"] == "merged"
    request = channel.calls[0]
    assert request["action"] == "sync"
    assert request["value"]["source_ref"] == MAIN
    assert request["config"]["target_ref"] == WORKSPACE_REF


@pytest.mark.asyncio
async def test_sync_takes_an_agent_branch_for_review():
    git, channel, _ = workspace([{"status": "conflict", "paths": ["note.md"]}])
    result = await git.sync(ROW, "refs/heads/agents/one")
    assert result["paths"] == ["note.md"]
    assert channel.calls[0]["value"]["source_ref"] == "refs/heads/agents/one"


@pytest.mark.asyncio
async def test_sync_refuses_anything_but_a_branch():
    git, _, _ = workspace([])
    with pytest.raises(WorkspaceError) as error:
        await git.sync(ROW, "refs/tags/v1")
    assert error.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["sync", "finish", "abort", "publish"])
async def test_files_never_change_under_a_running_task(operation):
    git, channel, _ = workspace([], active=True)
    with pytest.raises(WorkspaceError) as error:
        await getattr(git, operation)(ROW)
    assert error.value.detail == {"code": "workspace_busy"} or "workspace_busy" in str(error.value)
    assert channel.calls == []


@pytest.mark.asyncio
async def test_a_sync_exchange_cannot_write():
    git, channel, _ = workspace([{"status": "up_to_date"}])
    await git.sync(ROW)
    with pytest.raises(PermissionError):
        await channel.git_handler({"path": "/git/project.git/git-receive-pack", "method": "POST"})


@pytest.mark.asyncio
async def test_save_pushes_only_the_workspace_branch():
    git, channel, service = workspace(
        [{"status": "saved", "tip": "c" * 40}, {"status": "pushed", "tip": "c" * 40}]
    )
    assert await git.save(ROW) == {"status": "saved", "tip": "c" * 40}
    assert [call["action"] for call in channel.calls] == ["save", "publish"]
    assert channel.calls[1]["value"] == {"destination_ref": WORKSPACE_REF}
    service.transport.result.assert_called_once()


@pytest.mark.asyncio
async def test_save_with_nothing_new_pushes_nothing():
    git, channel, _ = workspace([{"status": "saved", "tip": "a" * 40}])
    assert (await git.save(ROW))["status"] == "saved"
    assert [call["action"] for call in channel.calls] == ["save"]


@pytest.mark.asyncio
async def test_a_waiting_merge_is_not_saved():
    git, channel, _ = workspace([{"status": "merging", "tip": "a" * 40}])
    assert (await git.save(ROW))["status"] == "merging"
    assert len(channel.calls) == 1


@pytest.mark.asyncio
async def test_publish_moves_the_workspace_then_the_project_branch():
    git, channel, _ = workspace(
        [
            {"status": "saved", "tip": "c" * 40},
            {"status": "pushed", "tip": "c" * 40},
            {"status": "pushed", "tip": "c" * 40},
        ]
    )
    assert await git.publish(ROW) == {"status": "published", "tip": "c" * 40}
    destinations = [c["value"]["destination_ref"] for c in channel.calls if c["action"] == "publish"]
    assert destinations == [WORKSPACE_REF, MAIN]


@pytest.mark.asyncio
async def test_publish_behind_the_project_asks_to_sync_first():
    git, _, _ = workspace(
        [
            {"status": "saved", "tip": "c" * 40},
            {"status": "pushed", "tip": "c" * 40},
            {"status": "rejected", "tip": "c" * 40},
        ]
    )
    assert (await git.publish(ROW))["status"] == "rejected"


@pytest.mark.asyncio
async def test_publish_with_the_project_already_there_changes_nothing():
    git, channel, _ = workspace([{"status": "saved", "tip": "b" * 40}, {"status": "pushed"}])
    assert (await git.publish(ROW))["status"] == "no_changes"
    assert sum(call["action"] == "publish" for call in channel.calls) == 1


@pytest.mark.asyncio
async def test_an_older_worker_is_refused_before_any_git():
    git, channel, _ = workspace([])
    channel.control = AsyncMock(return_value={})
    with pytest.raises(WorkspaceError) as error:
        await git.sync(ROW)
    assert error.value.status_code == 409
    channel.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_lost_publication_receipt_is_not_reported_as_done():
    git, _, service = workspace(
        [{"status": "saved", "tip": "c" * 40}, {"status": "pushed", "tip": "c" * 40}]
    )
    service.transport.result = Mock(return_value=None)
    with pytest.raises(RuntimeError, match="outcome unavailable"):
        await git.save(ROW)
