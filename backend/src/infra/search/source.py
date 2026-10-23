"""Authorized, version-consistent source for one indexing operation."""

from contextlib import asynccontextmanager

from src.file_operations.async_queries import open_read_session
from src.platform.authorization.lifetime import OperationAuthorization
from src.platform.authorization.models import ProjectAction


class IndexSource:
    def __init__(self, project_id, reads, authorization):
        self._project_id, self._reads, self._authorization = project_id, reads, authorization

    async def entries(self, path):
        await self._authorization.current()
        return await self._reads.call(lambda read: read.list_tree(self._project_id, path))

    async def content(self, path):
        await self._authorization.current()
        return await self._reads.call(lambda read: read.read_file(self._project_id, path))

    async def check_live(self):
        # User permission renewal and native GC pin renewal are separate checks.
        await self._authorization.current()
        await self._reads.call(lambda read: read.get_head_commit_id(self._project_id))


@asynccontextmanager
async def open_index_source(ops, authorization, project_id, user_id):
    authority = OperationAuthorization(
        authorization, project_id, user_id, ProjectAction.CONTENT_READ
    )
    grant = await authority.current()
    async with open_read_session(ops.for_grant(grant), project_id) as reads:
        yield IndexSource(project_id, reads, authority)
