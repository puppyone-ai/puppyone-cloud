"""Grant-bound history reads over canonical native Git objects.

Synchronous Version Engine reads. HTTP callers own each read at the edge
(deadline-bound ``Execution`` and disconnect polling).
"""

from __future__ import annotations


class VersionAdminService:
    """Grant-bound administrative reads of standard Git objects."""

    def __init__(self, repo_manager, grant=None):
        self._repos, self._grant = repo_manager, grant

    def for_grant(self, grant):
        return type(self)(self._repos, grant)

    def init_tree(self, project_id):
        from src.version_engine.write_engine.engine import VersionWriteEngine

        return VersionWriteEngine(self._repos).initialize_project_tree(project_id)

    def _read(self, project_id, callback):
        from src.version_engine.read.native_history import NativeHistory
        from src.version_engine.read.native_tree_reader import open_tree_read

        with open_tree_read(self._repos, project_id, self._grant, bulk=True) as reader:
            return callback(NativeHistory(reader.snapshot))

    def get_history_view(self, project_id, path=None, limit=50, since_commit_id=""):
        return self._read(
            project_id,
            lambda h: (
                h.linear(limit, since_commit_id, path),
                h.head,
                h.snapshot.revision().tree_oid,
            ),
        )

    def get_commit_history(self, project_id, path=None, limit=50, since_commit_id=""):
        return self._read(project_id, lambda h: h.linear(limit, since_commit_id, path))

    def get_project_head_commit_id(self, project_id):
        return self._read(project_id, lambda h: h.head)

    def get_commit_parent_ids(self, project_id, commit_ids):
        return self._read(
            project_id,
            lambda h: {oid: h.require(oid)["parent_ids"] for oid in commit_ids},
        )

    def get_commit_content(self, project_id, path, commit_id):
        return self._read(project_id, lambda h: h.content(commit_id, path))

    def compute_diff(self, project_id, from_commit_id, to_commit_id):
        return self._read(project_id, lambda h: h.diff(from_commit_id, to_commit_id))
