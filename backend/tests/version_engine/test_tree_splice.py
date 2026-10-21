"""Tests for tree_splice — direct Git-tree mutations.

The splice primitives are the foundation of every typed write op
(write_file, delete, mkdir, move, …) once we route those through
ProductOperationAdapter. They must be
correct under all the edge cases — empty scope, missing intermediates,
folder moves, idempotent repeats, etc.

These tests use an in-memory ``ObjectStore`` (``FileSystemBackend`` over
``tmp_path``) — no S3, no Supabase — so they're cheap to run and isolate
purely the tree-shape logic.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from src.version_engine.write_engine import tree as tree_mod
from src.version_engine.storage.object_store import ObjectStore

from src.version_engine.write_engine.tree_patch import (
    splice_batch,
    splice_copy,
    splice_mkdir,
    splice_move,
    splice_multi_put_refs,
    splice_put_blob,
    splice_put_blob_ref,
    splice_remove,
    splice_touch,
)


# ══════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════


@pytest.fixture
def store(tmp_path) -> ObjectStore:
    obj_dir = tmp_path / "objects"
    obj_dir.mkdir()
    return ObjectStore(obj_dir)


@pytest.fixture
def empty_root(store: ObjectStore) -> str:
    """A fresh empty scope (canonical empty-tree hash)."""
    return tree_mod.write_tree(store, {})


def _files(store: ObjectStore, root: str) -> dict[str, bytes]:
    """Flatten a tree to ``{path: content_bytes}`` for assertions."""
    flat = tree_mod.tree_to_flat(store, root)
    return {p: store.get(h) for p, h in flat.items()}


def _paths(store: ObjectStore, root: str) -> set[str]:
    return set(tree_mod.tree_to_flat(store, root).keys())


# ══════════════════════════════════════════════════
# splice_put_blob
# ══════════════════════════════════════════════════


class TestPutBlob:
    """write_file semantics: create / update / mkdir-p intermediate dirs."""

    def test_put_into_empty_tree(self, store, empty_root):
        new_root, changes = splice_put_blob(store, empty_root, "hello.md", b"hi")

        assert new_root != empty_root
        assert _files(store, new_root) == {"hello.md": b"hi"}
        assert changes == [("add", "hello.md")]

    def test_put_creates_intermediate_dirs(self, store, empty_root):
        new_root, changes = splice_put_blob(
            store, empty_root, "a/b/c.md", b"deep",
        )

        assert _files(store, new_root) == {"a/b/c.md": b"deep"}
        assert changes == [("add", "a/b/c.md")]

    def test_put_alongside_existing_sibling(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"alpha")
        r2, changes = splice_put_blob(store, r1, "b.md", b"beta")

        assert _files(store, r2) == {"a.md": b"alpha", "b.md": b"beta"}
        assert changes == [("add", "b.md")]

    def test_put_rejects_non_git_raw_json_root(self, store):
        old_blob = b"old"
        old_blob_hash = hashlib.sha1(old_blob).hexdigest()
        old_tree_raw = json.dumps(
            {"old.md": ["B", old_blob_hash]},
            sort_keys=True,
        ).encode("utf-8")
        old_root = hashlib.sha1(old_tree_raw).hexdigest()
        store.put_loose(old_blob_hash, old_blob)
        store.put_loose(old_root, old_tree_raw)

        with pytest.raises(Exception):
            splice_put_blob(store, old_root, "new.md", b"new")

    def test_put_update_existing(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "x.md", b"old")
        r2, changes = splice_put_blob(store, r1, "x.md", b"new")

        assert _files(store, r2) == {"x.md": b"new"}
        assert changes == [("update", "x.md")]

    def test_put_idempotent_same_content_is_noop(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "x.md", b"same")
        r2, changes = splice_put_blob(store, r1, "x.md", b"same")

        assert r2 == r1
        assert changes == []

    def test_put_preserves_unrelated_subtree(self, store, empty_root):
        # Pre-populate /docs/* and /code/foo.py.
        r1, _ = splice_put_blob(store, empty_root, "docs/intro.md", b"A")
        r2, _ = splice_put_blob(store, r1, "docs/api.md", b"B")
        r3, _ = splice_put_blob(store, r2, "code/foo.py", b"C")

        # Update one file in /docs.
        r4, changes = splice_put_blob(store, r3, "docs/intro.md", b"A2")

        assert _files(store, r4) == {
            "docs/intro.md": b"A2",
            "docs/api.md": b"B",
            "code/foo.py": b"C",
        }
        assert changes == [("update", "docs/intro.md")]

    def test_put_through_existing_file_raises(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"file")

        with pytest.raises(ValueError, match="is a file"):
            splice_put_blob(store, r1, "a.md/inner.md", b"oops")

    def test_put_over_existing_dir_raises(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a/b.md", b"nested")

        with pytest.raises(ValueError, match="currently a directory"):
            splice_put_blob(store, r1, "a", b"oops")

    def test_put_empty_path_raises(self, store, empty_root):
        with pytest.raises(ValueError):
            splice_put_blob(store, empty_root, "", b"")
        with pytest.raises(ValueError):
            splice_put_blob(store, empty_root, "/", b"")


# ══════════════════════════════════════════════════
# splice_remove
# ══════════════════════════════════════════════════


class TestRemove:
    """Delete semantics: file, folder, idempotent."""

    def test_remove_single_file(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")
        r2, _ = splice_put_blob(store, r1, "b.md", b"")

        r3, changes = splice_remove(store, r2, ["a.md"])

        assert _paths(store, r3) == {"b.md"}
        assert changes == [("delete", "a.md")]

    def test_remove_nested_file_prunes_empty_parent(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a/b/c.md", b"")
        r2, _ = splice_put_blob(store, r1, "x.md", b"")

        r3, _ = splice_remove(store, r2, ["a/b/c.md"])

        # The now-empty /a/b and /a should be pruned, leaving only /x.md.
        assert _paths(store, r3) == {"x.md"}

    def test_remove_folder_drops_subtree(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "docs/intro.md", b"A")
        r2, _ = splice_put_blob(store, r1, "docs/api.md", b"B")
        r3, _ = splice_put_blob(store, r2, "code/foo.py", b"C")

        r4, changes = splice_remove(store, r3, ["docs"])

        assert _paths(store, r4) == {"code/foo.py"}
        deleted = {c[1] for c in changes if c[0] == "delete"}
        assert deleted == {"docs/intro.md", "docs/api.md"}

    def test_remove_broken_folder_entry_does_not_read_missing_subtree(
        self,
        store,
        empty_root,
    ):
        missing_tree_hash = "1" * 40
        r1 = tree_mod.write_tree(store, {
            "broken": ["T", missing_tree_hash],
            "ok.md": ["B", tree_mod.write_blob(store, b"ok")],
        })

        r2, changes = splice_remove(store, r1, ["broken"])

        assert _paths(store, r2) == {"ok.md"}
        assert changes == [("delete", "broken")]

    def test_remove_broken_file_entry_does_not_read_missing_blob(
        self,
        store,
        empty_root,
    ):
        missing_blob_hash = "2" * 40
        r1 = tree_mod.write_tree(store, {
            "broken.md": ["B", missing_blob_hash],
            "ok.md": ["B", tree_mod.write_blob(store, b"ok")],
        })

        r2, changes = splice_remove(store, r1, ["broken.md"])

        assert _paths(store, r2) == {"ok.md"}
        assert changes == [("delete", "broken.md")]

    def test_remove_missing_path_is_noop(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")

        r2, changes = splice_remove(store, r1, ["nonexistent.md"])

        assert r2 == r1
        assert changes == []

    def test_remove_missing_intermediate_is_noop(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")

        r2, changes = splice_remove(store, r1, ["nope/inner.md"])

        assert r2 == r1
        assert changes == []

    def test_remove_multiple_paths(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")
        r2, _ = splice_put_blob(store, r1, "b.md", b"")
        r3, _ = splice_put_blob(store, r2, "c.md", b"")

        r4, changes = splice_remove(store, r3, ["a.md", "c.md"])

        assert _paths(store, r4) == {"b.md"}
        assert {c[1] for c in changes} == {"a.md", "c.md"}

    def test_remove_all_files_yields_empty_tree(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "only.md", b"")

        r2, _ = splice_remove(store, r1, ["only.md"])

        assert _paths(store, r2) == set()


# ══════════════════════════════════════════════════
# splice_move
# ══════════════════════════════════════════════════


class TestMove:
    """move / rename semantics."""

    def test_move_file_within_same_dir(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"hello")

        r2, changes = splice_move(store, r1, "a.md", "b.md")

        assert _files(store, r2) == {"b.md": b"hello"}
        actions = {c[0] for c in changes}
        assert actions == {"delete", "add"}

    def test_move_file_across_dirs(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "src/foo.md", b"x")

        r2, _ = splice_move(store, r1, "src/foo.md", "dst/bar.md")

        assert _files(store, r2) == {"dst/bar.md": b"x"}

    def test_move_folder_relocates_subtree_without_blob_reads(self, store, empty_root):
        # Build a 3-file folder.
        r1, _ = splice_put_blob(store, empty_root, "old/a.md", b"A")
        r2, _ = splice_put_blob(store, r1, "old/b.md", b"B")
        r3, _ = splice_put_blob(store, r2, "old/sub/c.md", b"C")

        r4, changes = splice_move(store, r3, "old", "new")

        assert _files(store, r4) == {
            "new/a.md": b"A",
            "new/b.md": b"B",
            "new/sub/c.md": b"C",
        }
        # Every file in the subtree should appear in changes (delete @ old, add @ new).
        deleted = {c[1] for c in changes if c[0] == "delete"}
        added = {c[1] for c in changes if c[0] == "add"}
        assert deleted == {"old/a.md", "old/b.md", "old/sub/c.md"}
        assert added == {"new/a.md", "new/b.md", "new/sub/c.md"}

    def test_move_into_own_subtree_is_rejected(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "old/sub/c.md", b"C")

        with pytest.raises(ValueError, match="own subtree"):
            splice_move(store, r1, "old", "old/sub/old")

        assert _files(store, r1) == {"old/sub/c.md": b"C"}

    def test_move_to_nested_directory(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "report.md", b"data")

        r2, changes = splice_move(store, r1, "report.md", "archive/report_123")

        assert _files(store, r2) == {"archive/report_123": b"data"}
        assert ("delete", "report.md") in changes
        assert ("add", "archive/report_123") in changes

    def test_move_overwrites_destination(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"alpha")
        r2, _ = splice_put_blob(store, r1, "b.md", b"beta")

        r3, changes = splice_move(store, r2, "a.md", "b.md")

        assert _files(store, r3) == {"b.md": b"alpha"}
        # Changes should record both the deleted source AND the overwritten dst.
        deleted = [c[1] for c in changes if c[0] == "delete"]
        assert "a.md" in deleted
        assert "b.md" in deleted  # overwrite of pre-existing dst
        assert ("add", "b.md") in changes

    def test_move_missing_source_raises(self, store, empty_root):
        with pytest.raises(FileNotFoundError):
            splice_move(store, empty_root, "no-such-file", "dst.md")

    def test_move_self_is_noop(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")

        r2, changes = splice_move(store, r1, "a.md", "a.md")

        assert r2 == r1
        assert changes == []


# ══════════════════════════════════════════════════
# splice_copy
# ══════════════════════════════════════════════════


class TestCopy:
    """copy semantics: duplicate references without blob rewrites."""

    def test_copy_file(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"hello")
        before = store.count()[0]

        r2, changes = splice_copy(store, r1, "a.md", "b.md")
        after = store.count()[0]

        assert _files(store, r2) == {"a.md": b"hello", "b.md": b"hello"}
        assert changes == [("add", "b.md")]
        assert after - before <= 1

    def test_copy_folder_reuses_subtree(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "docs/a.md", b"A")
        r2, _ = splice_put_blob(store, r1, "docs/sub/b.md", b"B")
        before = store.count()[0]

        r3, changes = splice_copy(store, r2, "docs", "docs-copy")
        after = store.count()[0]

        assert _files(store, r3) == {
            "docs/a.md": b"A",
            "docs/sub/b.md": b"B",
            "docs-copy/a.md": b"A",
            "docs-copy/sub/b.md": b"B",
        }
        assert sorted(changes) == sorted([
            ("add", "docs-copy/a.md"),
            ("add", "docs-copy/sub/b.md"),
        ])
        assert after - before <= 1

    def test_copy_overwrites_destination(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"alpha")
        r2, _ = splice_put_blob(store, r1, "b.md", b"beta")

        r3, changes = splice_copy(store, r2, "a.md", "b.md")

        assert _files(store, r3) == {"a.md": b"alpha", "b.md": b"alpha"}
        assert ("delete", "b.md") in changes
        assert ("add", "b.md") in changes

    def test_copy_missing_source_raises(self, store, empty_root):
        with pytest.raises(FileNotFoundError):
            splice_copy(store, empty_root, "missing.md", "dst.md")

    def test_copy_directory_into_itself_raises(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "docs/a.md", b"A")

        with pytest.raises(ValueError, match="into itself"):
            splice_copy(store, r1, "docs", "docs/sub/docs")


# ══════════════════════════════════════════════════
# splice_touch
# ══════════════════════════════════════════════════


class TestTouch:
    def test_touch_existing_file_returns_same_root_with_update_change(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"A")

        r2, changes = splice_touch(store, r1, ["a.md"])

        assert r2 == r1
        assert changes == [("update", "a.md")]

    def test_touch_missing_file_raises(self, store, empty_root):
        with pytest.raises(FileNotFoundError):
            splice_touch(store, empty_root, ["missing.md"])

    def test_touch_directory_raises(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "docs/a.md", b"A")

        with pytest.raises(ValueError, match="is a directory"):
            splice_touch(store, r1, ["docs"])


# ══════════════════════════════════════════════════
# splice_mkdir
# ══════════════════════════════════════════════════


class TestMkdir:
    def test_mkdir_creates_keep_marker(self, store, empty_root):
        r1, changes = splice_mkdir(store, empty_root, "newdir")

        assert _paths(store, r1) == {"newdir/.keep"}
        assert changes == [("add", "newdir/.keep")]

    def test_mkdir_existing_folder_is_noop(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "dir/file.md", b"")

        r2, changes = splice_mkdir(store, r1, "dir")

        assert r2 == r1
        assert changes == []

    def test_mkdir_nested_path(self, store, empty_root):
        r1, changes = splice_mkdir(store, empty_root, "a/b/c")

        assert _paths(store, r1) == {"a/b/c/.keep"}
        assert changes == [("add", "a/b/c/.keep")]

    def test_mkdir_over_existing_file_raises(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "a.md", b"")

        with pytest.raises(ValueError, match="currently holds a file"):
            splice_mkdir(store, r1, "a.md")


# ══════════════════════════════════════════════════
# splice_batch
# ══════════════════════════════════════════════════


class TestBatch:
    def test_batch_combines_puts_and_removes(self, store, empty_root):
        r1, _ = splice_put_blob(store, empty_root, "old.md", b"")

        r2, changes = splice_batch(store, r1, [
            ("put", "new1.md", b"alpha"),
            ("put", "new2.md", b"beta"),
            ("rm", "old.md"),
        ])

        assert _files(store, r2) == {"new1.md": b"alpha", "new2.md": b"beta"}
        actions = sorted(changes)
        assert ("add", "new1.md") in actions
        assert ("add", "new2.md") in actions
        assert ("delete", "old.md") in actions

    def test_batch_empty_is_noop(self, store, empty_root):
        r1, changes = splice_batch(store, empty_root, [])

        assert r1 == empty_root
        assert changes == []

    def test_batch_unknown_op_raises(self, store, empty_root):
        with pytest.raises(ValueError, match="unknown batch op"):
            splice_batch(store, empty_root, [("bogus", "x.md")])

    def test_batch_with_put_ref(self, store, empty_root):
        """Batch with ``put_ref`` ops should commit by hash without
        the caller passing bytes — the blob is staged externally."""
        # Pre-stage two blobs as if they came from S3 CopyObject.
        h1 = store.put(b"alpha")
        h2 = store.put(b"beta")

        r1, changes = splice_batch(store, empty_root, [
            ("put_ref", "a.md", h1),
            ("put_ref", "b.md", h2),
        ])

        assert _files(store, r1) == {"a.md": b"alpha", "b.md": b"beta"}
        actions = sorted(changes)
        assert ("add", "a.md") in actions
        assert ("add", "b.md") in actions

    def test_batch_mixed_put_and_put_ref(self, store, empty_root):
        """``put`` (bytes) and ``put_ref`` (hash) can mix freely;
        both produce the same tree shape."""
        h_pre = store.put(b"prestaged")

        r1, _ = splice_batch(store, empty_root, [
            ("put", "in_memory.md", b"in-memory"),
            ("put_ref", "from_s3.md", h_pre),
        ])

        assert _files(store, r1) == {
            "in_memory.md": b"in-memory",
            "from_s3.md": b"prestaged",
        }


# ══════════════════════════════════════════════════
# splice_put_blob_ref — hash-first single-file put
# ══════════════════════════════════════════════════


class TestPutBlobRef:
    """``splice_put_blob_ref`` is the byte-free counterpart to
    ``splice_put_blob``: takes a blob hash that's already in the
    store and points the tree at it. Crucial for the multipart
    upload path where the bytes never enter the Python process."""

    def test_put_ref_into_empty_tree(self, store, empty_root):
        h = store.put(b"hello")

        new_root, changes = splice_put_blob_ref(
            store, empty_root, "hi.md", h,
        )

        assert _files(store, new_root) == {"hi.md": b"hello"}
        assert changes == [("add", "hi.md")]

    def test_put_ref_creates_intermediate_dirs(self, store, empty_root):
        h = store.put(b"deep")
        new_root, changes = splice_put_blob_ref(
            store, empty_root, "a/b/c.md", h,
        )

        assert _files(store, new_root) == {"a/b/c.md": b"deep"}
        assert changes == [("add", "a/b/c.md")]

    def test_put_ref_update_existing(self, store, empty_root):
        h_old = store.put(b"old")
        h_new = store.put(b"new")
        r1, _ = splice_put_blob_ref(store, empty_root, "x.md", h_old)
        r2, changes = splice_put_blob_ref(store, r1, "x.md", h_new)

        assert _files(store, r2) == {"x.md": b"new"}
        assert changes == [("update", "x.md")]

    def test_put_ref_idempotent(self, store, empty_root):
        h = store.put(b"same")
        r1, _ = splice_put_blob_ref(store, empty_root, "x.md", h)
        r2, changes = splice_put_blob_ref(store, r1, "x.md", h)

        assert r2 == r1
        assert changes == []

    def test_put_ref_equivalent_to_put_blob(self, store, empty_root):
        """``splice_put_blob_ref`` with a hash from ``store.put`` must
        produce the same tree as ``splice_put_blob`` with the same
        bytes — they're two paths to the same content-addressed
        commit, must agree on the result."""
        content = b"the quick brown fox"

        # Path A: byte API.
        r_bytes, _ = splice_put_blob(store, empty_root, "doc.md", content)

        # Path B: stage first, then ref.
        h = store.put(content)
        r_ref, _ = splice_put_blob_ref(store, empty_root, "doc.md", h)

        assert r_bytes == r_ref

    def test_put_ref_over_existing_dir_raises(self, store, empty_root):
        h = store.put(b"x")
        r1, _ = splice_put_blob(store, empty_root, "a/b.md", b"x")

        with pytest.raises(ValueError, match="path is currently a directory"):
            splice_put_blob_ref(store, r1, "a", h)

    def test_put_ref_empty_blob_hash_raises(self, store, empty_root):
        with pytest.raises(ValueError, match="blob_hash is required"):
            splice_put_blob_ref(store, empty_root, "x.md", "")

    def test_put_ref_empty_path_raises(self, store, empty_root):
        h = store.put(b"x")
        with pytest.raises(ValueError, match="empty path"):
            splice_put_blob_ref(store, empty_root, "", h)


# ══════════════════════════════════════════════════
# Performance / scaling property
# ══════════════════════════════════════════════════


class TestScaling:
    """Confirm splice ops touch only O(D) tree nodes, not O(N) files."""

    def test_put_only_writes_spine_nodes(self, store, empty_root):
        # Pre-populate 100 files in /docs/.
        cur = empty_root
        for i in range(100):
            cur, _ = splice_put_blob(store, cur, f"docs/file{i:03d}.md", b"x")

        before = store.count()[0]

        # Update one file. New objects: 1 blob + 2 trees (docs + root).
        new_cur, _ = splice_put_blob(store, cur, "docs/file042.md", b"y")
        after = store.count()[0]

        # 100 pre-existing files share all but the one we changed; the
        # update should add at most ~3 new objects (blob + 2 spine trees).
        # Anything more than ~5 means we accidentally re-encoded the
        # whole subtree.
        assert after - before <= 5, (
            f"single-file update wrote {after - before} new objects; "
            "should be ~3 (blob + spine)"
        )

    def test_folder_move_does_not_rewrite_subtree(self, store, empty_root):
        # Build a 50-file subtree.
        cur = empty_root
        for i in range(50):
            cur, _ = splice_put_blob(store, cur, f"old/file{i:03d}.md", b"x")
        before = store.count()[0]

        new_cur, _ = splice_move(store, cur, "old", "new")
        after = store.count()[0]

        # A folder rename moves the existing /old subtree pointer to /new.
        # It needs at most 2 new tree nodes (one to drop "old" from root,
        # one to add "new" to root). ALL the file blobs and inner tree
        # nodes are reused.
        assert after - before <= 3, (
            f"folder rename wrote {after - before} new objects; "
            "should be ~2 (root rebuild only)"
        )

    def test_put_ref_writes_no_new_blob(self, store, empty_root):
        """Hash-first commit: ``splice_put_blob_ref`` must not write a
        blob (the blob is already staged). Only spine tree nodes
        should be added.

        This is the load-bearing property for the multipart upload
        optimization — if it regresses, we'd silently re-write
        every file blob in the Python process and lose all the
        savings we got from CopyObject."""
        prestaged = store.put(b"large file content")  # +1 blob
        before = store.count()[0]

        new_root, _ = splice_put_blob_ref(
            store, empty_root, "deep/nested/path.dat", prestaged,
        )
        after = store.count()[0]

        # Expected new objects: 3 tree nodes for spine (deep/, deep/nested/,
        # root). Zero new blob objects — we trust the existing one.
        delta = after - before
        assert delta <= 4, (
            f"put_ref wrote {delta} new objects; should be ≤4 (spine only). "
            f"If this is much higher, we accidentally re-wrote the blob."
        )

    def test_batch_groups_same_parent_into_single_tree_write(
        self, store, empty_root,
    ):
        """The load-bearing optimization: 3 files dropped into the
        SAME folder must produce exactly ONE new ``docdocs/`` tree
        node, not three (one per put). Each extra tree write is a
        ~1s round-trip to Supabase Storage, so this directly drives
        the user's perceived 14-second 'finalizing' hang.

        Pre-fix behavior: ``splice_batch`` called ``splice_put_blob_ref``
        per op, rebuilding the spine for each file → 6 tree writes
        for 3 files (3 docdocs/ versions × 2 spine nodes).
        Post-fix: 2 tree writes total (1 docdocs/ + 1 root).
        """
        # Pre-stage 3 blobs as if they were CopyObject'd from S3.
        h1 = store.put(b"file 1 content")
        h2 = store.put(b"file 2 content")
        h3 = store.put(b"file 3 content")
        # Snapshot count AFTER staging — we want to measure the
        # tree-write cost only, not the blob writes.
        before = store.count()[0]

        new_root, changes = splice_batch(store, empty_root, [
            ("put_ref", "docdocs/file1.md", h1),
            ("put_ref", "docdocs/file2.md", h2),
            ("put_ref", "docdocs/file3.md", h3),
        ])

        after = store.count()[0]

        # Expected: 1 docdocs/ tree + 1 root tree = 2 new objects.
        # If this regresses to 6+, the per-parent grouping broke.
        delta = after - before
        assert delta == 2, (
            f"3 files in same folder wrote {delta} new tree nodes; "
            f"should be exactly 2 (1 docdocs/ + 1 root)."
        )

        # Sanity: tree contents are still correct.
        assert _files(store, new_root) == {
            "docdocs/file1.md": b"file 1 content",
            "docdocs/file2.md": b"file 2 content",
            "docdocs/file3.md": b"file 3 content",
        }
        # And changes list has exactly 3 adds.
        assert sorted(changes) == sorted([
            ("add", "docdocs/file1.md"),
            ("add", "docdocs/file2.md"),
            ("add", "docdocs/file3.md"),
        ])

    def test_batch_groups_multiple_parents_independently(
        self, store, empty_root,
    ):
        """Files spanning multiple parent dirs each get one tree
        write per dir. Specifically: 5 files in /docs/photos/ + 3
        files in /docdocs/ + 1 in /readme.md should produce:
        photos/ (1) + docs/ (1) + docdocs/ (1) + root (1) = 4 tree
        writes — NOT 9 (one per file)."""
        hashes = [store.put(f"content {i}".encode()) for i in range(9)]
        before = store.count()[0]

        new_root, _ = splice_batch(store, empty_root, [
            ("put_ref", "docs/photos/p1.jpg", hashes[0]),
            ("put_ref", "docs/photos/p2.jpg", hashes[1]),
            ("put_ref", "docs/photos/p3.jpg", hashes[2]),
            ("put_ref", "docs/photos/p4.jpg", hashes[3]),
            ("put_ref", "docs/photos/p5.jpg", hashes[4]),
            ("put_ref", "docdocs/a.md", hashes[5]),
            ("put_ref", "docdocs/b.md", hashes[6]),
            ("put_ref", "docdocs/c.md", hashes[7]),
            ("put_ref", "readme.md", hashes[8]),
        ])
        after = store.count()[0]

        delta = after - before
        # 4 tree nodes: photos/, docs/, docdocs/, root.
        # If we got 9+, each file is rebuilding spines independently.
        assert delta == 4, (
            f"Multi-parent batch wrote {delta} tree nodes; "
            f"expected 4 (photos/, docs/, docdocs/, root)."
        )

        # Sanity: every file is reachable.
        files = _files(store, new_root)
        assert len(files) == 9
        assert files["docs/photos/p1.jpg"] == b"content 0"
        assert files["docdocs/c.md"] == b"content 7"
        assert files["readme.md"] == b"content 8"

    def test_batch_groups_byte_puts_too(self, store, empty_root):
        """``("put", path, bytes)`` ops also benefit from grouping.
        We hash the bytes via ``tree_mod.write_blob`` (same as the
        per-op path did internally), then queue the resulting hash
        into the same batched spine rebuild as ``put_ref`` ops.

        For a folder upload that comes through the byte API (e.g.
        connector / template / CLI ``fs write`` of multiple files),
        we still get the 1-parent-tree-write win."""
        before = store.count()[0]

        new_root, _ = splice_batch(store, empty_root, [
            ("put", "src/a.py", b"alpha"),
            ("put", "src/b.py", b"beta"),
            ("put", "src/c.py", b"gamma"),
        ])
        after = store.count()[0]

        delta = after - before
        # 3 blobs + 1 src/ tree + 1 root tree = 5 new objects.
        # If we got 7+, per-op grouping isn't kicking in.
        assert delta == 5, (
            f"3 byte-puts wrote {delta} new objects; "
            f"expected 5 (3 blobs + src/ + root)."
        )
        assert _files(store, new_root) == {
            "src/a.py": b"alpha",
            "src/b.py": b"beta",
            "src/c.py": b"gamma",
        }

    def test_batch_does_not_cross_rm_boundary(self, store, empty_root):
        """Reordering across an ``rm`` boundary would change the
        end-state for cases like ``put a/x; rm a; put a/y`` — the
        ``rm a`` would or wouldn't see ``a/x`` depending on
        ordering. The batched grouping must NOT reorder around
        ``rm``s, so ``put a/x`` is flushed BEFORE the ``rm a``
        runs.

        Property tested: after this batch, ``a/`` contains only
        ``a/y``. If the puts were eagerly grouped together first,
        we'd see both x and y, or neither, depending on the
        implementation bug."""
        h_x = store.put(b"x content")
        h_y = store.put(b"y content")

        # First seed an unrelated file so the rm has something
        # ancestor-shaped to remove.
        seeded, _ = splice_put_blob(store, empty_root, "a/seed.md", b"seed")

        new_root, _ = splice_batch(store, seeded, [
            ("put_ref", "a/x.md", h_x),  # added under existing a/
            ("rm",      "a"),            # wipes everything under a/
            ("put_ref", "a/y.md", h_y),  # re-creates a/ with just y
        ])

        files = _files(store, new_root)
        assert files == {"a/y.md": b"y content"}, (
            f"rm boundary not respected; files = {files}"
        )

    def test_batch_does_not_cross_mv_boundary(self, store, empty_root):
        """Same property as the rm boundary, but for moves: a
        ``put`` queued before an ``mv`` of the put's destination
        ancestor must commit BEFORE the move (so the move sees
        the put's target), not after."""
        h_x = store.put(b"x content")
        h_y = store.put(b"y content")

        new_root, _ = splice_batch(store, empty_root, [
            ("put_ref", "src/a.md", h_x),
            ("mv",      "src", "dest"),
            ("put_ref", "src/b.md", h_y),
        ])

        files = _files(store, new_root)
        # First put + mv → dest/a.md.
        # Second put after mv → src/b.md (in fresh src/).
        assert files == {
            "dest/a.md": b"x content",
            "src/b.md":  b"y content",
        }, f"mv boundary not respected; files = {files}"


# ══════════════════════════════════════════════════
# splice_multi_put_refs — direct unit tests
# ══════════════════════════════════════════════════


class TestMultiPutRefs:
    """Direct tests for the batched put primitive. ``splice_batch``
    invokes this for any run of put/put_ref ops, but it's worth
    asserting the contract directly so future refactors don't
    quietly violate it."""

    def test_empty_items_is_noop(self, store, empty_root):
        new_root, changes = splice_multi_put_refs(store, empty_root, [])
        assert new_root == empty_root
        assert changes == []

    def test_all_idempotent_is_noop(self, store, empty_root):
        h = store.put(b"same")
        seeded, _ = splice_multi_put_refs(
            store, empty_root, [("a.md", h), ("b.md", h)],
        )
        before = store.count()[0]

        # Re-apply identical refs; entries are unchanged so no tree
        # write should fire.
        new_root, changes = splice_multi_put_refs(
            store, seeded, [("a.md", h), ("b.md", h)],
        )
        after = store.count()[0]

        assert new_root == seeded
        assert changes == []
        assert after == before, "no-op batch wrote new objects"

    def test_partial_idempotent(self, store, empty_root):
        """Mix of new and unchanged items: only the new entries
        cause tree rewrites."""
        h_a = store.put(b"a")
        h_b_old = store.put(b"b old")
        h_b_new = store.put(b"b new")
        seeded, _ = splice_multi_put_refs(
            store, empty_root, [("a.md", h_a), ("b.md", h_b_old)],
        )

        new_root, changes = splice_multi_put_refs(
            store, seeded, [("a.md", h_a), ("b.md", h_b_new)],
        )

        assert new_root != seeded
        assert changes == [("update", "b.md")]

    def test_writing_through_existing_dir_raises(self, store, empty_root):
        """Trying to put a file at a path currently occupied by a
        directory must raise — clearly, with the offending path
        in the message."""
        h = store.put(b"x")
        seeded, _ = splice_multi_put_refs(
            store, empty_root, [("dir/inner.md", h)],
        )

        with pytest.raises(ValueError, match="path is currently a directory"):
            splice_multi_put_refs(store, seeded, [("dir", h)])

    def test_descending_through_existing_file_raises(
        self, store, empty_root,
    ):
        """Trying to put a/b.md when a/ is currently a FILE must
        raise — both this and the previous case share the same
        intent ('don't silently corrupt the type system'), but
        the failure path is distinct."""
        h_file = store.put(b"file at a")
        h_blob = store.put(b"blob")
        seeded, _ = splice_multi_put_refs(store, empty_root, [("a", h_file)])

        with pytest.raises(ValueError, match="currently a file"):
            splice_multi_put_refs(store, seeded, [("a/b.md", h_blob)])

    def test_empty_path_raises(self, store, empty_root):
        h = store.put(b"x")
        with pytest.raises(ValueError, match="empty path"):
            splice_multi_put_refs(store, empty_root, [("", h)])

    def test_empty_hash_raises(self, store, empty_root):
        with pytest.raises(ValueError, match="blob_hash is required"):
            splice_multi_put_refs(store, empty_root, [("a.md", "")])

    def test_validation_errors_dont_partial_write(
        self, store, empty_root,
    ):
        """Validation errors are raised eagerly during a parsing
        pass, BEFORE any tree node is written. So a 47-item batch
        with a bad item at index 30 leaves the store totally
        untouched (modulo the blobs that the caller pre-staged
        anyway)."""
        good_hash = store.put(b"good")
        before = store.count()[0]

        with pytest.raises(ValueError):
            splice_multi_put_refs(store, empty_root, [
                ("a.md", good_hash),
                ("b.md", good_hash),
                ("",     good_hash),  # malformed — should abort
                ("c.md", good_hash),
            ])

        after = store.count()[0]
        assert after == before, (
            "validation error caused partial tree writes "
            "(should be all-or-nothing)"
        )
