"""Read-only product operations and their explicit snapshot lifetime.

No request grant or reader is kept in a process singleton or ContextVar.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING

from src.infra.execution.admission import read_admission
from src.infra.execution.runner import run_owned

if TYPE_CHECKING:
    from src.file_operations.operation_adapter import ProductOperationAdapter
    from src.version_engine.read.native_tree_reader import NativeTreeReader


class ProductQueries:
    """Read surface shared by one-shot adapters and operation-owned sessions."""

    def _read(self, method, project_id, *args, **kwargs):
        raise NotImplementedError

    def read_file(self, project_id, path):
        return self._read("read_file", project_id, path)

    def read_file_range(self, project_id, path, **kwargs):
        return self._read("read_file_range", project_id, path, **kwargs)

    def list_dir(self, project_id, path="", **kwargs):
        return self._read("list_dir", project_id, path, **kwargs)

    def list_tree(self, project_id, path="", max_depth=-1, **kwargs):
        return self._read("list_tree", project_id, path, max_depth, **kwargs)

    def stat(self, project_id, path, **kwargs):
        return self._read("stat", project_id, path, **kwargs)

    def get_head_commit_id(self, project_id):
        return self._read("get_head_commit_id", project_id)

    def get_root_hash(self, project_id):
        return self._read("get_root_hash", project_id)

    def get_read_revision(self, project_id):
        return self._read("get_read_revision", project_id)

    def get_scope_head_commit_id(self, project_id, scope_path):
        if scope_path:
            raise PermissionError("native Scope operations are not enabled")
        return self.get_head_commit_id(project_id)

    def get_scope_head_commit_id_for_path(self, project_id, path):
        return self.get_head_commit_id(project_id)

    def _scope_read(self, method, project_id, scope, *args, **kwargs):
        if scope:
            raise PermissionError("native Scope operations are not enabled")
        return self._read(method, project_id, *args, **kwargs)

    def read_file_in_scope(self, project_id, scope, path):
        return self._scope_read("read_file", project_id, scope, path)

    def read_file_range_in_scope(self, project_id, scope, path, **kwargs):
        return self._scope_read("read_file_range", project_id, scope, path, **kwargs)

    def list_dir_in_scope(self, project_id, scope, path="", **kwargs):
        return self._scope_read("list_dir", project_id, scope, path, **kwargs)

    def list_tree_in_scope(self, project_id, scope, path="", max_depth=-1, **kwargs):
        return self._scope_read("list_tree", project_id, scope, path, max_depth, **kwargs)

    def stat_in_scope(self, project_id, scope, path, **kwargs):
        return self._scope_read("stat", project_id, scope, path, **kwargs)

    def get_path_timestamps(self, project_id, paths):
        return self._read("get_path_timestamps", project_id, paths)


class ProductReadSession(ProductQueries):
    def __init__(self, reader: NativeTreeReader):
        self._reader = reader
        self._closed = False

    def _read(self, method, project_id, *args, **kwargs):
        if self._closed:
            raise RuntimeError("Read session is closed")
        return getattr(self._reader, method)(project_id, *args, **kwargs)


@contextmanager
def open_query_session(ops: ProductOperationAdapter, project_id: str):
    with ops.open_read(project_id, ops._bound_grant(project_id)) as reader:
        session = ProductReadSession(reader)
        try:
            yield session
        finally:
            session._closed = True


async def run_read[T](
    ops: ProductOperationAdapter, project_id: str, query: Callable[[ProductReadSession], T]
) -> T:
    """Own admission, worker, snapshot and cleanup for one complete read."""

    def execute():
        with ops.read_session(project_id) as session:
            return query(session)

    async with read_admission():
        return await run_owned(execute)
