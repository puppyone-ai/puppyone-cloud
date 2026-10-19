from types import SimpleNamespace

import pytest

from src.version_engine.domain.intents import ProjectWriteState
from src.file_operations.commands import VersionWriteCommandService


class _FakeOps:
    def __init__(self):
        self.calls = []

    async def write_file(self, project_id, path, content, **kwargs):
        self.calls.append(("write_file", project_id, path, content, kwargs))
        return SimpleNamespace(commit_id="c-write", merged=False, conflicts=0)

    async def delete(self, project_id, paths, **kwargs):
        self.calls.append(("delete", project_id, paths, kwargs))
        return SimpleNamespace(commit_id="c-delete", merged=False, conflicts=0)

    async def bulk_write(self, project_id, files, **kwargs):
        self.calls.append(("bulk_write", project_id, files, kwargs))
        return SimpleNamespace(commit_id="c-bulk", merged=False, conflicts=0)


@pytest.mark.asyncio
async def test_product_write_serializes_json_and_carries_write_state():
    ops = _FakeOps()
    commands = VersionWriteCommandService(ops)
    write_state = ProjectWriteState(
        project_id="proj",
        project_name="Project",
        role="editor",
        can_write=True,
        root_hash="root",
        head_commit_id="head",
    )

    outcome = await commands.write_file(
        "proj",
        "data/users",
        {"users": []},
        node_type="json",
        actor="user:u1",
        default_message_prefix="edit",
        project_write_state=write_state,
    )

    assert outcome.path == "data/users.json"
    kind, project_id, path, content, kwargs = ops.calls[0]
    assert kind == "write_file"
    assert project_id == "proj"
    assert path == "data/users.json"
    assert b'"users": []' in content
    assert kwargs == {
        "who": "user:u1",
        "message": "edit data/users.json",
        "project_write_state": write_state,
    }


@pytest.mark.asyncio
async def test_scoped_write_sets_scope_and_deferred_projection():
    ops = _FakeOps()
    commands = VersionWriteCommandService(ops)

    outcome = await commands.write_file(
        "proj",
        "notes/readme",
        "# hi",
        node_type="markdown",
        actor="ap:user",
        scope="docs",
        default_message_prefix="ap write",
        base_commit_id="base",
        defer_projection=True,
    )

    assert outcome.path == "notes/readme.md"
    _kind, _project_id, path, _content, kwargs = ops.calls[0]
    assert path == "notes/readme.md"
    assert kwargs == {
        "who": "ap:user",
        "message": "ap write notes/readme.md",
        "scope": "docs",
        "base_commit_id": "base",
        "defer_projection": True,
    }


@pytest.mark.asyncio
async def test_root_delete_does_not_leak_scoped_kwargs():
    ops = _FakeOps()
    commands = VersionWriteCommandService(ops)

    await commands.delete(
        "proj",
        ["docs/a.md"],
        actor="mcp_agent",
        message="delete docs/a.md",
    )

    assert ops.calls == [
        (
            "delete",
            "proj",
            ["docs/a.md"],
            {"who": "mcp_agent", "message": "delete docs/a.md"},
        )
    ]


@pytest.mark.asyncio
async def test_bulk_write_normalizes_each_file_once():
    ops = _FakeOps()
    commands = VersionWriteCommandService(ops)

    outcome = await commands.bulk_write(
        "proj",
        {
            "rows/users": {"rows": []},
            "docs/intro": "hello",
        },
        node_types={
            "rows/users": "json",
            "docs/intro": "markdown",
        },
        actor="sync",
        message="import",
    )

    assert outcome.paths == ["rows/users.json", "docs/intro.md"]
    _kind, _project_id, files, kwargs = ops.calls[0]
    assert set(files) == {"rows/users.json", "docs/intro.md"}
    assert kwargs == {
        "deleted": [],
        "who": "sync",
        "message": "import",
    }
