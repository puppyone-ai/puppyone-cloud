"""ScopedFsService execution + access-control tests.

The 16 fs_* tools and (more importantly) the gating in `call()` — writable check,
per-endpoint tool whitelist, path exclusion — plus the conflict mapping were
untested. These guard what an EXTERNAL MCP client (ChatGPT/Claude Desktop) is
allowed to do inside a scope, so they're worth pinning. Uses fake ops/commands so
no live version-engine is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.version_engine.scoped_fs.context import ScopedFsContext
from src.version_engine.scoped_fs.errors import ScopedFsError, ScopedFsPermissionDenied
from src.version_engine.scoped_fs.service import ScopedFsService
from src.version_engine.write_engine.errors import ConcurrentMutationError


class _FakeOps:
    """ProductOperationAdapter stand-in — enough for read happy-paths."""

    def list_dir_in_scope(self, *_a, **_k):
        return []

    def list_tree_in_scope(self, *_a, **_k):
        return []

    def stat_in_scope(self, *_a, **_k):
        return None

    def get_scope_head_commit_id(self, *_a, **_k):
        return "head-c1"


class _FakeCommands:
    """VersionWriteCommandService stand-in. `conflict=True` makes writes raise the
    concurrent-mutation error so we can assert the 409 mapping."""

    def __init__(self, conflict: bool = False):
        self.conflict = conflict
        self.writes: list[tuple] = []

    async def write_file(self, project_id, path, content, **kwargs):
        self.writes.append((project_id, path, content, kwargs))
        if self.conflict:
            raise ConcurrentMutationError(
                scope_path=kwargs.get("scope", ""),
                expected_head_commit_id="old",
                current_head_commit_id="new",
            )
        return SimpleNamespace(path=path, result=SimpleNamespace(commit_id="commit-2"))


def _ctx(*, mode="rw", exclude=None, allowed_tools=None) -> ScopedFsContext:
    return ScopedFsContext(
        api_key="mcp_test",
        endpoint_id="ep-1",
        endpoint_name="Test",
        project_id="proj-1",
        user_id="user-1",
        scope_id="scope-1",
        scope_path="docs",
        mode=mode,
        exclude=exclude or [],
        allowed_tools=allowed_tools,
    )


def _svc(conflict=False) -> ScopedFsService:
    return ScopedFsService(_FakeOps(), _FakeCommands(conflict=conflict))


# ── access-control gates (raised in call() before any I/O) ────────────

async def test_unknown_tool_rejected():
    with pytest.raises(ScopedFsError) as ei:
        await _svc().call(_ctx(), "fs_bogus", {})
    assert ei.value.code == "UNKNOWN_TOOL" and ei.value.status_code == 400


async def test_write_tool_denied_on_readonly_scope():
    with pytest.raises(ScopedFsPermissionDenied) as ei:
        await _svc().call(_ctx(mode="ro"), "fs_write", {"path": "a.md", "content": "x"})
    assert ei.value.status_code == 403 and "writable" in ei.value.message


async def test_tool_not_in_whitelist_denied():
    # endpoint allows only fs_ls → fs_cat is disabled for it
    with pytest.raises(ScopedFsPermissionDenied) as ei:
        await _svc().call(_ctx(allowed_tools=frozenset({"fs_ls"})), "fs_cat", {"path": "a.md"})
    assert "disabled" in ei.value.message


async def test_excluded_path_denied():
    ctx = _ctx(exclude=["secret.txt"], allowed_tools=frozenset({"fs_cat"}))
    with pytest.raises(ScopedFsPermissionDenied) as ei:
        await _svc().call(ctx, "fs_cat", {"path": "secret.txt"})
    assert ei.value.status_code == 403 and "excluded" in ei.value.message


# ── happy paths + conflict mapping ────────────────────────────────────

async def test_ls_empty_root_ok():
    out = await _svc().call(_ctx(allowed_tools=frozenset({"fs_ls"})), "fs_ls", {})
    assert out["entries"] == [] and out["scope"]["id"] == "scope-1"
    assert out["head_commit_id"] == "head-c1"


async def test_write_commits_and_returns_commit_id():
    svc = _svc()
    out = await svc.call(_ctx(allowed_tools=frozenset({"fs_write"})),
                         "fs_write", {"path": "a.md", "content": "hello"})
    assert out["commit_id"] == "commit-2" and out["path"] == "a.md"
    assert out["scope"]["mode"] == "rw"
    # the write was forwarded with the mcp source channel + actor
    _, _, _, kwargs = svc.commands.writes[0]
    assert kwargs["source_channel"] == "mcp" and kwargs["actor"] == "user-1"


async def test_write_conflict_maps_to_409():
    with pytest.raises(ScopedFsError) as ei:
        await _svc(conflict=True).call(_ctx(allowed_tools=frozenset({"fs_write"})),
                                       "fs_write", {"path": "a.md", "content": "x"})
    assert ei.value.code == "CONFLICT" and ei.value.status_code == 409


# ── exclude path-space: admission stores excludes scope-absolute ──────
# `_ctx` uses scope_path="docs", so a scope-absolute exclude carries that prefix.

async def test_excluded_path_denied_absolute_form():
    # The exclude entry is scope-absolute (project-relative), matching the
    # admission layer. A DIRECT fs_cat of the scope-relative path must still be
    # denied — the bug was that _clean_path compared scope-relative paths only,
    # so a known excluded path could be read directly while listings hid it.
    ctx = _ctx(exclude=["docs/secret.txt"], allowed_tools=frozenset({"fs_cat"}))
    with pytest.raises(ScopedFsPermissionDenied):
        await _svc().call(ctx, "fs_cat", {"path": "secret.txt"})


# ── fs_grep: exclusion gate + scope-relative read ────────────────────

class _GrepOps(_FakeOps):
    """Ops fake for grep: returns tree entries + file contents, records reads."""

    def __init__(self, entries, contents):
        self._entries = entries
        self._contents = contents
        self.reads: list[str] = []

    def list_tree_in_scope(self, *_a, **_k):
        return self._entries

    def read_file_in_scope(self, _project_id, _scope_path, path):
        self.reads.append(path)
        return self._contents.get(path, b"")


def _entry(path, typ="md", size=32):
    return SimpleNamespace(
        name=path.rsplit("/", 1)[-1], path=path, type=typ, size_bytes=size,
        content_hash="h", mime_type="text/plain", children_count=0,
        integrity_status="ok", created_at=None, modified_at=None,
    )


async def test_grep_skips_excluded_files_and_reads_scope_relative():
    entries = [_entry("docs/secret.txt"), _entry("docs/note.txt")]
    ops = _GrepOps(entries, {"note.txt": b"hello world\n", "secret.txt": b"hello secret\n"})
    svc = ScopedFsService(ops, _FakeCommands())
    ctx = _ctx(exclude=["docs/secret.txt"], allowed_tools=frozenset({"fs_grep"}))
    out = await svc.call(ctx, "fs_grep", {"pattern": "hello"})
    # Excluded file is never opened, and the visible file is read via its
    # scope-RELATIVE path (not the scope-absolute entry.path that would
    # double-prefix the scope and silently match nothing).
    assert ops.reads == ["note.txt"]
    assert out["matches"] and all(m["path"] == "note.txt" for m in out["matches"])


# ── fs_cat on a folder is an error (not an ls payload) ───────────────

class _FolderOps(_FakeOps):
    def stat_in_scope(self, *_a, **_k):
        return _entry("docs/sub", typ="folder")


async def test_cat_on_folder_raises_is_directory():
    svc = ScopedFsService(_FolderOps(), _FakeCommands())
    with pytest.raises(ScopedFsError) as ei:
        await svc.call(_ctx(allowed_tools=frozenset({"fs_cat"})), "fs_cat", {"path": "sub"})
    assert ei.value.code == "IS_DIRECTORY"


# ── write cannot smuggle an excluded path via the node-type extension ─

async def test_write_extension_cannot_bypass_exclude():
    # exclude hides notes.json; writing "notes" as json serializes to notes.json,
    # which the post-canonicalization gate must still deny (never reach the write).
    svc = _svc()
    ctx = _ctx(exclude=["docs/notes.json"], allowed_tools=frozenset({"fs_write"}))
    with pytest.raises(ScopedFsPermissionDenied):
        await svc.call(ctx, "fs_write", {"path": "notes", "content": "x", "node_type": "json"})
    assert svc.commands.writes == []
