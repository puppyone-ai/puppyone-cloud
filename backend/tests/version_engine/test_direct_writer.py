"""Integration tests for the typed-write path: ProductOperationAdapter + direct_writer.

These tests exercise the full happy path of every typed op
(``write_file``, ``delete``, ``mkdir``, ``move``, ``bulk_write``) against a real
``PuppyOneServerRepo`` whose history/audit/scope managers are
in-memory fakes from ``test_server_repo``. No Supabase, no S3 —
just the orchestration logic + the tree splice + CAS dance.

What we're verifying:
- The new direct_writer path produces the same observable
  end-state as hash's old ``handle_push`` would have for typed ops.
- Idempotent writes don't create commits (no audit pollution).
- Each typed op records the correct ``op_type`` in audit.
- ``record_history`` gets a ``changes`` list with full project-root
  paths.
- Product writes publish one root commit synchronously; derived projection
  hooks are scheduled off-path so Save is not gated by scope-view refresh.
- Folder ``move`` / ``delete`` do NOT re-encode the subtree — the same
  blob/tree hashes are reused.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.version_engine.write_engine import tree as tree_mod
from src.version_engine.write_engine.git_object_format import encode_commit, encode_tree
from src.version_engine.storage.object_store import ObjectStore

from src.version_engine.write_engine.engine import ConcurrentMutationError
from src.version_engine.adapters.product.operation_adapter import BlobRef, MissingBlobError, ProductOperationAdapter
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager

from tests.version_engine.test_server_repo import (
    FakeAuditManager,
    FakeHistoryManager,
)


# ══════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════


@pytest.fixture
def memory_store(tmp_path) -> ObjectStore:
    obj_dir = tmp_path / "objects"
    obj_dir.mkdir()
    return ObjectStore(obj_dir)


@pytest.fixture(autouse=True)
def _isolate_derived_hooks(monkeypatch):
    """Typed-write tests are hermetic and must not contact Supabase/S3.

    Derived-hook behavior has dedicated tests; scheduling a real background
    hook here leaks threads into asyncio teardown and makes this in-memory
    suite wait on whatever cloud URL happens to be in the developer's env.
    """
    from src.version_engine.derived import hooks

    monkeypatch.setattr(hooks, "schedule_post_project_update_hook", lambda *_a, **_k: None)


@pytest.fixture
def server_repo(memory_store):
    """A PuppyOneServerRepo backed by in-memory fakes."""
    from src.version_engine.infrastructure.supabase.scope_manager import ScopeManager
    from src.version_engine.infrastructure.supabase.server_repo import PuppyOneServerRepo

    history = FakeHistoryManager()
    audit = FakeAuditManager()

    class FakeScopeBackend:
        def __init__(self):
            self._scopes = {}

        def get(self, sid):
            return self._scopes.get(sid)

        def put(self, sid, scope):
            self._scopes[sid] = scope

        def delete(self, sid):
            return self._scopes.pop(sid, None) is not None

        def list_all(self):
            return list(self._scopes.values())

    return PuppyOneServerRepo(
        project_id="test-proj",
        project_name="Test Project",
        store=memory_store,
        history=history,
        audit=audit,
        scopes=ScopeManager(FakeScopeBackend()),
    )


@pytest.fixture
def ops(server_repo) -> ProductOperationAdapter:
    """A ProductOperationAdapter instance wired to a single in-memory project."""
    repo_manager = MagicMock(spec=VersionRepoManager)
    repo_manager.get_server_repo.return_value = server_repo
    return ProductOperationAdapter(repo_manager)


def _files_in_root(server_repo) -> dict[str, bytes]:
    """Read back the current root scope as ``{path: content}``."""
    root_hash = server_repo.get_scope_hash("")
    if not root_hash:
        return {}
    flat = tree_mod.tree_to_flat(server_repo.store, root_hash)
    return {p: server_repo.store.get(h) for p, h in flat.items()}


def _files_for_hash(server_repo, root_hash: str) -> dict[str, bytes]:
    if not root_hash:
        return {}
    flat = tree_mod.tree_to_flat(server_repo.store, root_hash)
    return {p: server_repo.store.get(h) for p, h in flat.items()}


def _commit_count(server_repo) -> int:
    return len(server_repo.history._entries)


def _audit_events(server_repo) -> list[dict]:
    return list(server_repo.audit.events)


def _last_audit(server_repo) -> dict:
    events = _audit_events(server_repo)
    return events[-1] if events else {}


def _make_linear_commit_chain(server_repo, *, length: int) -> tuple[str, str]:
    """Create a deep Git parent chain without warming compatibility caches."""

    tree_hash = server_repo.store.put_tree(encode_tree([]))
    parent = ""
    for index in range(length):
        commit_body = encode_commit(
            tree_sha1=tree_hash,
            parent_sha1=parent or None,
            author="Test <test@puppyone>",
            author_time=f"{1_700_000_000 + index} +0000",
            committer="Test <test@puppyone>",
            committer_time=f"{1_700_000_000 + index} +0000",
            message=f"seed {index}\n",
        )
        parent = server_repo.store.put_commit(commit_body)
    return tree_hash, parent


def _run_project_projection(ops: ProductOperationAdapter, server_repo) -> None:
    from src.version_engine.derived.hooks import run_project_root_visibility_barrier

    entry = server_repo.history._entries[-1]
    run_project_root_visibility_barrier(
        "test-proj",
        ops._repos,
        {
            "status": "ok",
            "commit_id": entry["commit_id"],
            "root": entry["root_hash"],
        },
        raise_errors=True,
    )


# ══════════════════════════════════════════════════
# write_file
# ══════════════════════════════════════════════════


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_creates_file_in_root_scope(self, ops, server_repo):
        result = await ops.write_file(
            "test-proj", "hello.md", b"hi", who="user:test",
        )

        assert result.commit_id != ""
        assert _files_in_root(server_repo) == {"hello.md": b"hi"}
        assert _commit_count(server_repo) == 1

    @pytest.mark.asyncio
    async def test_audit_logs_typed_op_name(self, ops, server_repo):
        await ops.write_file(
            "test-proj", "x.md", b"x", who="user:bob",
        )

        last = _last_audit(server_repo)
        assert last["type"] == "write_file"
        assert last["agent"] == "user:bob"
        assert last["detail"]["path"] == "x.md"
        assert last["detail"]["size"] == 1

    @pytest.mark.asyncio
    async def test_history_changes_have_full_paths(self, ops, server_repo):
        await ops.write_file(
            "test-proj", "docs/readme.md", b"hello", who="user:test",
        )

        entry = server_repo.history._entries[-1]
        assert entry["changes"] == [
            {"path": "docs/readme.md", "action": "add"},
        ]

    @pytest.mark.asyncio
    async def test_product_write_under_child_scope_stays_one_root_commit(
        self, ops, server_repo,
    ):
        server_repo.add_scope("docs-scope", "/docs/")

        result = await ops.write_file(
            "test-proj", "docs/a.md", b"a", who="user:web",
        )

        assert _commit_count(server_repo) == 1
        entry = server_repo.history._entries[-1]
        assert entry["commit_id"] == result.commit_id
        assert entry["scope_path"] == ""
        assert entry["root_hash"] == server_repo.get_root_hash()
        assert _files_in_root(server_repo) == {"docs/a.md": b"a"}

        _run_project_projection(ops, server_repo)
        docs_hash = server_repo.get_scope_hash("docs")
        docs_head = server_repo.get_scope_head_commit_id("docs")
        assert docs_hash
        assert docs_head
        assert docs_head != result.commit_id
        assert _files_for_hash(server_repo, docs_hash) == {"a.md": b"a"}

    @pytest.mark.asyncio
    async def test_idempotent_repeat_creates_no_commit(self, ops, server_repo):
        await ops.write_file(
            "test-proj", "x.md", b"same", who="user:test",
        )
        commits_after_first = _commit_count(server_repo)

        result = await ops.write_file(
            "test-proj", "x.md", b"same", who="user:test",
        )

        assert _commit_count(server_repo) == commits_after_first
        assert result.commit_id == ""  # no-op

    @pytest.mark.asyncio
    async def test_update_records_update_action(self, ops, server_repo):
        await ops.write_file(
            "test-proj", "x.md", b"v1", who="user:test",
        )
        await ops.write_file(
            "test-proj", "x.md", b"v2", who="user:test",
        )

        last_entry = server_repo.history._entries[-1]
        assert last_entry["changes"] == [
            {"path": "x.md", "action": "update"},
        ]

    @pytest.mark.asyncio
    async def test_stale_base_commit_rejected_without_overwrite(self, ops, server_repo):
        first = await ops.write_file(
            "test-proj", "x.md", b"v1", who="user:test",
        )
        second = await ops.write_file(
            "test-proj", "x.md", b"v2", who="user:test",
            base_commit_id=first.commit_id,
        )
        commits_after_second = _commit_count(server_repo)

        with pytest.raises(ConcurrentMutationError) as exc:
            await ops.write_file(
                "test-proj", "x.md", b"stale", who="user:test",
                base_commit_id=first.commit_id,
            )

        assert exc.value.expected_head_commit_id == first.commit_id
        assert exc.value.current_head_commit_id == second.commit_id
        assert _commit_count(server_repo) == commits_after_second
        assert _files_in_root(server_repo) == {"x.md": b"v2"}

    @pytest.mark.asyncio
    async def test_product_save_does_not_walk_parent_history(
        self, ops, server_repo, monkeypatch,
    ):
        tree_hash, head = _make_linear_commit_chain(server_repo, length=160)
        server_repo.history.set_root_hash(tree_hash)
        server_repo.history.set_scope_hash("", tree_hash)
        server_repo.history.set_scope_head_commit_id("", head)
        server_repo.history.set_head_commit_id(head)

        original_get_object = server_repo.store.get_object
        object_reads: list[str] = []

        def counted_get_object(object_id: str):
            object_reads.append(object_id)
            return original_get_object(object_id)

        monkeypatch.setattr(server_repo.store, "get_object", counted_get_object)

        await ops.write_file(
            "test-proj", "save.md", b"fast", who="user:web",
        )

        assert len(object_reads) < 40
        assert head in object_reads


# ══════════════════════════════════════════════════
# delete
# ══════════════════════════════════════════════════


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_one_file(self, ops, server_repo):
        await ops.write_file("test-proj", "a.md", b"a", who="u")
        await ops.write_file("test-proj", "b.md", b"b", who="u")

        await ops.delete("test-proj", ["a.md"], who="u")

        assert set(_files_in_root(server_repo).keys()) == {"b.md"}

    @pytest.mark.asyncio
    async def test_delete_folder_drops_subtree_without_blob_reads(
        self, ops, server_repo, memory_store,
    ):
        # Write 50 files inside /docs.
        for i in range(50):
            await ops.write_file(
                "test-proj", f"docs/f{i:03d}.md", b"x", who="u",
            )

        before = memory_store.count()[0]
        await ops.delete("test-proj", ["docs"], who="u")
        after = memory_store.count()[0]

        # Hard delete is just an unlink-from-root operation. Removing
        # the "docs" entry from the root tree writes ONE new tree node
        # (the rebuilt root). All 50 file blobs and the inner tree
        # nodes remain in CAS storage but are unreachable.
        assert after - before <= 2, (
            f"folder delete wrote {after - before} new objects; "
            "should be ~1 (root rebuild)"
        )
        assert _files_in_root(server_repo) == {}

    @pytest.mark.asyncio
    async def test_product_folder_delete_clears_child_scope_projection(
        self, ops, server_repo,
    ):
        server_repo.add_scope("docs-scope", "/docs/")
        await ops.write_file("test-proj", "docs/a.md", b"a", who="user:web")
        _run_project_projection(ops, server_repo)

        await ops.delete("test-proj", ["docs"], who="user:web")
        _run_project_projection(ops, server_repo)

        assert _commit_count(server_repo) == 2
        assert _files_in_root(server_repo) == {}
        assert server_repo.get_scope_hash("docs") == ""
        assert server_repo.get_scope_head_commit_id("docs") == ""
        assert server_repo.history._entries[-1]["scope_path"] == ""


# ══════════════════════════════════════════════════
# mkdir
# ══════════════════════════════════════════════════


class TestMkdir:
    @pytest.mark.asyncio
    async def test_creates_keep_marker(self, ops, server_repo):
        await ops.mkdir("test-proj", "newdir", who="u")

        files = _files_in_root(server_repo)
        assert "newdir/.keep" in files

    @pytest.mark.asyncio
    async def test_existing_folder_is_noop(self, ops, server_repo):
        await ops.write_file(
            "test-proj", "dir/inside.md", b"x", who="u",
        )
        commits_before = _commit_count(server_repo)

        result = await ops.mkdir("test-proj", "dir", who="u")

        assert _commit_count(server_repo) == commits_before
        assert result.commit_id == ""


# ══════════════════════════════════════════════════
# move
# ══════════════════════════════════════════════════


class TestMove:
    @pytest.mark.asyncio
    async def test_renames_file(self, ops, server_repo):
        await ops.write_file("test-proj", "a.md", b"x", who="u")

        await ops.move("test-proj", "a.md", "b.md", who="u")

        assert _files_in_root(server_repo) == {"b.md": b"x"}

    @pytest.mark.asyncio
    async def test_folder_move_does_not_re_upload_blobs(
        self, ops, server_repo, memory_store,
    ):
        for i in range(20):
            await ops.write_file(
                "test-proj", f"old/f{i:02d}.md", b"x", who="u",
            )

        before = memory_store.count()[0]
        await ops.move("test-proj", "old", "new", who="u")
        after = memory_store.count()[0]

        # Folder rename should write at most a couple of new tree
        # nodes (root rebuild only). Existing blobs and the moved
        # subtree are reused unchanged.
        assert after - before <= 3, (
            f"folder rename wrote {after - before} new objects; "
            "should be ~2 (root rebuild)"
        )

    @pytest.mark.asyncio
    async def test_folder_move_into_own_descendant_is_rejected(
        self, ops, server_repo,
    ):
        await ops.write_file("test-proj", "old/sub/file.md", b"x", who="u")

        with pytest.raises(ValueError, match="own subtree"):
            await ops.move("test-proj", "old", "old/sub/old", who="u")

        assert _files_in_root(server_repo) == {"old/sub/file.md": b"x"}

    @pytest.mark.asyncio
    async def test_audit_records_move_op(self, ops, server_repo):
        await ops.write_file("test-proj", "a.md", b"", who="u")

        await ops.move("test-proj", "a.md", "b.md", who="u")

        last = _last_audit(server_repo)
        assert last["type"] == "move"
        assert last["detail"]["old_path"] == "a.md"
        assert last["detail"]["new_path"] == "b.md"


# ══════════════════════════════════════════════════
# bulk_write
# ══════════════════════════════════════════════════


class TestBulkWrite:
    @pytest.mark.asyncio
    async def test_combined_writes_and_deletes_in_one_commit(
        self, ops, server_repo,
    ):
        await ops.write_file("test-proj", "old.md", b"", who="u")
        commits_before = _commit_count(server_repo)

        await ops.bulk_write(
            "test-proj",
            files={"new1.md": b"a", "new2.md": b"b"},
            deleted=["old.md"],
            who="u",
        )

        assert _commit_count(server_repo) == commits_before + 1
        files = _files_in_root(server_repo)
        assert files == {"new1.md": b"a", "new2.md": b"b"}


# ══════════════════════════════════════════════════
# Hash-first APIs: stage_blob_from_bytes + bulk_write_refs
# ══════════════════════════════════════════════════


class TestStageBlobFromBytes:
    """``stage_blob_from_bytes`` is the in-process staging entry
    point. It writes content to the project's ObjectStore and
    returns a ``BlobRef`` ready to feed to ``bulk_write_refs`` —
    without going through the apply_mutation/CAS/audit pipeline
    (which would be wasted work for a pure stage)."""

    @pytest.mark.asyncio
    async def test_stage_returns_correct_hash_and_size(
        self, ops, server_repo,
    ):
        from src.version_engine.write_engine.git_object_format import hash_object

        ref = await ops.stage_blob_from_bytes("test-proj", b"hello world")

        assert isinstance(ref, BlobRef)
        assert ref.size == 11
        assert ref.hash == hash_object("blob", b"hello world")
        # The blob must actually be in the store now — that's the
        # whole point of staging.
        assert server_repo.store.exists(ref.hash)
        assert server_repo.store.get(ref.hash) == b"hello world"

    @pytest.mark.asyncio
    async def test_stage_does_not_create_commit(self, ops, server_repo):
        commits_before = _commit_count(server_repo)
        audit_before = len(_audit_events(server_repo))

        await ops.stage_blob_from_bytes("test-proj", b"some bytes")

        # A pure stage must NOT pollute history or audit. Stage is a
        # background prep step; the visible event happens later when
        # ``bulk_write_refs`` commits the tree change.
        assert _commit_count(server_repo) == commits_before
        assert len(_audit_events(server_repo)) == audit_before

    @pytest.mark.asyncio
    async def test_stage_is_idempotent(self, ops, server_repo):
        ref1 = await ops.stage_blob_from_bytes("test-proj", b"same")
        store_count_after_first = server_repo.store.count()[0]

        ref2 = await ops.stage_blob_from_bytes("test-proj", b"same")
        store_count_after_second = server_repo.store.count()[0]

        # Same content → same hash → no new object on the second put.
        assert ref1.hash == ref2.hash
        assert store_count_after_first == store_count_after_second


class TestBulkWriteRefs:
    """``bulk_write_refs`` is the hash-first commit primitive. It
    accepts blobs already staged (via ``stage_blob_from_bytes`` or
    ``stage_blob_from_s3``) and produces the same observable tree
    state as ``bulk_write(bytes)`` — but skips the per-file
    ``store.put`` round-trip."""

    @pytest.mark.asyncio
    async def test_commits_tree_referencing_staged_blobs(
        self, ops, server_repo,
    ):
        ref_a = await ops.stage_blob_from_bytes("test-proj", b"alpha")
        ref_b = await ops.stage_blob_from_bytes("test-proj", b"beta")

        result = await ops.bulk_write_refs(
            "test-proj",
            file_refs={"a.md": ref_a, "b.md": ref_b},
            who="u",
        )

        assert result.commit_id  # non-empty → an actual commit landed
        files = _files_in_root(server_repo)
        assert files == {"a.md": b"alpha", "b.md": b"beta"}

    @pytest.mark.asyncio
    async def test_equivalent_to_bulk_write_bytes(
        self, ops, server_repo,
    ):
        """The hash-first path and the byte path must produce the
        same final tree state — just via different intermediates."""
        # Path A: bytes-in.
        await ops.bulk_write(
            "test-proj",
            files={"x.md": b"alpha", "y.md": b"beta"},
            who="u",
        )
        files_via_bytes = _files_in_root(server_repo)
        # Reset by removing both files.
        await ops.bulk_write(
            "test-proj", files={}, deleted=["x.md", "y.md"], who="u",
        )

        # Path B: stage then ref-commit.
        ref_x = await ops.stage_blob_from_bytes("test-proj", b"alpha")
        ref_y = await ops.stage_blob_from_bytes("test-proj", b"beta")
        await ops.bulk_write_refs(
            "test-proj",
            file_refs={"x.md": ref_x, "y.md": ref_y},
            who="u",
        )
        files_via_refs = _files_in_root(server_repo)

        assert files_via_bytes == files_via_refs

    @pytest.mark.asyncio
    async def test_audits_total_size(self, ops, server_repo):
        ref_a = await ops.stage_blob_from_bytes("test-proj", b"hello")  # 5
        ref_b = await ops.stage_blob_from_bytes("test-proj", b"world!!!")  # 8

        await ops.bulk_write_refs(
            "test-proj",
            file_refs={"a.md": ref_a, "b.md": ref_b},
            who="u",
        )

        last = _last_audit(server_repo)
        # ``total_size`` is the new audit field that ``bulk_write_refs``
        # produces alongside ``writes`` / ``deletes``. It's how we
        # surface "uploaded N MB" to dashboards. The fake records the
        # audit dict under a ``detail`` key.
        detail = last.get("detail", {})
        assert detail.get("total_size") == 13
        assert detail.get("writes") == 2
        assert detail.get("deletes") == 0

    @pytest.mark.asyncio
    async def test_verify_blobs_rejects_dangling_ref(
        self, ops, server_repo,
    ):
        """``verify_blobs=True`` must HEAD every ref before commit
        and refuse to land a tree pointing at a missing blob.
        This is the safety net that prevents dangling commits when
        the upstream pipeline has a bug."""
        bogus_ref = BlobRef(hash="0" * 16, size=42)

        with pytest.raises(MissingBlobError, match="not present"):
            await ops.bulk_write_refs(
                "test-proj",
                file_refs={"x.md": bogus_ref},
                who="u",
                verify_blobs=True,
            )

        # No commit should have landed.
        assert _files_in_root(server_repo) == {}

    @pytest.mark.asyncio
    async def test_verify_blobs_false_skips_check(
        self, ops, server_repo,
    ):
        """When the caller has independently confirmed the blobs
        exist (e.g. just CopyObject'd them), ``verify_blobs=False``
        skips the HEAD round-trip. We verify by passing a real
        staged ref — should commit with no errors."""
        ref = await ops.stage_blob_from_bytes("test-proj", b"trusted")

        await ops.bulk_write_refs(
            "test-proj",
            file_refs={"a.md": ref},
            who="u",
            verify_blobs=False,
        )

        assert _files_in_root(server_repo) == {"a.md": b"trusted"}

    @pytest.mark.asyncio
    async def test_combines_writes_and_deletes(self, ops, server_repo):
        await ops.write_file("test-proj", "old.md", b"", who="u")
        ref = await ops.stage_blob_from_bytes("test-proj", b"new content")

        await ops.bulk_write_refs(
            "test-proj",
            file_refs={"new.md": ref},
            deleted=["old.md"],
            who="u",
        )

        assert _files_in_root(server_repo) == {"new.md": b"new content"}

    @pytest.mark.asyncio
    async def test_dedup_unique_hashes_in_verify(
        self, ops, server_repo,
    ):
        """Same blob at multiple paths (e.g. duplicate PDFs in a
        folder upload) should HEAD the hash exactly once during
        verification. This is observable indirectly: the test must
        not fail / time out for a 50-path-1-blob batch."""
        ref = await ops.stage_blob_from_bytes("test-proj", b"shared")

        await ops.bulk_write_refs(
            "test-proj",
            file_refs={f"copy{i}.md": ref for i in range(50)},
            who="u",
            verify_blobs=True,
        )

        files = _files_in_root(server_repo)
        assert len(files) == 50
        # Every path resolves to the same blob — content-addressing
        # at work.
        assert all(v == b"shared" for v in files.values())


# ══════════════════════════════════════════════════
# Concurrency: CAS retry under contention
# ══════════════════════════════════════════════════


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_cas_retry_recovers_when_scope_state_advances(
        self, ops, server_repo,
    ):
        """If the scope_hash shifts between our read and our CAS, the
        retry loop should re-read and re-apply on top of the new state.

        We simulate this by writing a sibling file out-of-band before
        the splice's CAS. Because the in-memory FakeHistoryManager runs
        synchronously, we can't truly race; instead we verify that two
        sequential writes against fresh state both produce commits and
        end up reflected in the final tree. (True race coverage lives
        in the multi-repo stress test.)
        """
        await ops.write_file("test-proj", "a.md", b"a", who="u")
        await ops.write_file("test-proj", "b.md", b"b", who="u")

        files = _files_in_root(server_repo)
        assert files == {"a.md": b"a", "b.md": b"b"}
        assert _commit_count(server_repo) == 2
