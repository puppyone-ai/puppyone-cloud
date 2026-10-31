"""Unit tests for the commit-delta text indexer (GAP-6).

Covers the clear-then-reindex reconciliation:

  - deleted paths purge their index rows (grep stops returning them),
  - directory deletes purge the whole subtree,
  - modified paths clear their previous-version rows before re-indexing,
  - the dedup key is the real Git blob OID (not a per-commit surrogate),
    so identical content across commits collapses to one row-set,
  - the freshness watermark advances even for delete-only commits.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from src.version_engine.derived import text_indexer
from src.version_engine.derived.text_indexer import (
    _commit_delta_content_hash,
    _is_delete_change,
    index_commit_delta,
)
from src.version_engine.write_engine.git_object_format import hash_object


@pytest.fixture
def fake_repo(monkeypatch):
    """Patch ``TextIndexRepository`` so the indexer talks to a mock.

    The indexer imports the repo class lazily inside the function, so we
    patch it on its defining module. ``index_blobs`` also constructs one
    lazily — patch that too so ``upsert_chunks`` lands on the same mock.
    """
    repo = MagicMock()
    repo.delete_file.return_value = 1  # pretend one row purged per call
    repo.upsert_chunks.side_effect = lambda **kw: len(list(kw["chunks"]))

    fake_module = types.SimpleNamespace(TextIndexRepository=lambda *a, **k: repo)
    monkeypatch.setitem(
        sys.modules,
        "src.version_engine.infrastructure.supabase.text_index_repository",
        fake_module,
    )
    return repo


class TestIsDeleteChange:
    def test_op_deleted(self):
        assert _is_delete_change({"path": "a", "op": "deleted"})

    def test_action_delete(self):
        assert _is_delete_change({"path": "a", "action": "delete"})

    def test_added_is_not_delete(self):
        assert not _is_delete_change({"path": "a", "op": "added"})

    def test_update_is_not_delete(self):
        assert not _is_delete_change({"path": "a", "action": "update"})


class TestContentHash:
    def test_uses_real_git_blob_oid_when_no_hash_carried(self):
        data = b"hello world\n"
        got = _commit_delta_content_hash({"path": "a"}, data)
        assert got == hash_object("blob", data)
        # NOT the old commit_id:path surrogate shape
        assert ":" not in got

    def test_prefers_carried_content_hash(self):
        got = _commit_delta_content_hash({"content_hash": "deadbeef"}, b"xyz")
        assert got == "deadbeef"

    def test_prefers_new_hash_alias(self):
        got = _commit_delta_content_hash({"new_hash": "cafef00d"}, b"xyz")
        assert got == "cafef00d"

    def test_identical_content_yields_identical_hash(self):
        a = _commit_delta_content_hash({"path": "a.txt"}, b"same bytes")
        b = _commit_delta_content_hash({"path": "b.txt"}, b"same bytes")
        assert a == b  # content-addressed: cross-commit/file dedupe works


class TestIndexCommitDelta:
    def test_empty_changes_is_noop(self, fake_repo):
        n = index_commit_delta(
            project_id="p", commit_id="c", changes=[], read_blob=lambda _p: b"",
        )
        assert n == 0
        fake_repo.delete_file.assert_not_called()

    def test_delete_purges_subtree_and_does_not_reindex(self, fake_repo):
        read_blob = MagicMock(side_effect=AssertionError("delete must not read"))
        index_commit_delta(
            project_id="p",
            commit_id="c1",
            changes=[{"path": "old/dir", "op": "deleted"}],
            read_blob=read_blob,
        )
        fake_repo.delete_file.assert_called_once_with(
            project_id="p", file_path="old/dir", include_subtree=True,
        )
        fake_repo.upsert_chunks.assert_not_called()

    def test_add_clears_then_indexes_with_oid(self, fake_repo):
        data = b"line one\nline two\n"
        index_commit_delta(
            project_id="p",
            commit_id="c2",
            changes=[{"path": "notes/a.md", "op": "added"}],
            read_blob=lambda _p: data,
        )
        # cleared exact path (not subtree) before reindex
        fake_repo.delete_file.assert_called_once_with(
            project_id="p", file_path="notes/a.md", include_subtree=False,
        )
        # upserted under the real blob OID
        kwargs = fake_repo.upsert_chunks.call_args.kwargs
        assert kwargs["file_path"] == "notes/a.md"
        assert kwargs["content_hash"] == hash_object("blob", data)

    def test_modify_clears_old_rows_before_reindex(self, fake_repo):
        index_commit_delta(
            project_id="p",
            commit_id="c3",
            changes=[{"path": "x.txt", "op": "modified"}],
            read_blob=lambda _p: b"new content\n",
        )
        # a modify must clear the prior version's rows
        assert fake_repo.delete_file.call_count == 1
        assert fake_repo.delete_file.call_args.kwargs["include_subtree"] is False

    def test_missing_blob_treated_as_delete(self, fake_repo):
        # change not tagged delete, but file no longer resolves
        index_commit_delta(
            project_id="p",
            commit_id="c4",
            changes=[{"path": "ghost", "op": "modified"}],
            read_blob=lambda _p: None,
        )
        fake_repo.delete_file.assert_called_once_with(
            project_id="p", file_path="ghost", include_subtree=True,
        )
        fake_repo.upsert_chunks.assert_not_called()

    def test_filenotfound_treated_as_delete(self, fake_repo):
        def boom(_p):
            raise FileNotFoundError(_p)

        index_commit_delta(
            project_id="p",
            commit_id="c5",
            changes=[{"path": "vanished", "op": "modified"}],
            read_blob=boom,
        )
        fake_repo.delete_file.assert_called_once_with(
            project_id="p", file_path="vanished", include_subtree=True,
        )

    def test_watermark_advances_even_for_delete_only_commit(self, fake_repo):
        index_commit_delta(
            project_id="p",
            commit_id="cdel",
            changes=[{"path": "gone.txt", "op": "deleted"}],
            read_blob=lambda _p: b"",
        )
        fake_repo.set_scope_freshness.assert_called_once_with(
            project_id="p", scope_path="", indexed_commit_id="cdel",
        )

    def test_duplicate_path_last_record_wins(self, fake_repo):
        # same path listed as add then delete (via two scopes) -> delete wins
        read_blob = MagicMock(return_value=b"data")
        index_commit_delta(
            project_id="p",
            commit_id="c6",
            changes=[
                {"path": "dup", "op": "added"},
                {"path": "dup", "op": "deleted"},
            ],
            read_blob=read_blob,
        )
        # delete branch never reads the blob
        read_blob.assert_not_called()
        fake_repo.delete_file.assert_called_once_with(
            project_id="p", file_path="dup", include_subtree=True,
        )
