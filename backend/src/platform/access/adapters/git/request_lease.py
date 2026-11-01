"""Fence every Git transport request with the Project write lease.

Reading a Git request and resolving its credential is this adapter's job; the
lease itself belongs to Project. Names are looked up on their modules at call
time, so a request always sees the resolver and lease currently installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from src.exceptions import AppException, ErrorCode
from src.platform.access.adapters.git import auth as git_auth
from src.platform.access.adapters.git import router as git_router
from src.platform.project import write_lease


async def git_project_write_lease(request: Request) -> AsyncIterator[None]:
    """Authorize, then fence every Git request for its full response lifetime.

    Clone/fetch requests are product reads, but they also materialize the
    Project-owned Git transport cache on the host.  They therefore participate
    in the same lifecycle drain as canonical mutations.  Read-only credentials
    remain valid for those requests; only receive-pack and explicit cache
    rebuild operations require a write-capable runtime grant.
    """

    path = request.url.path
    is_mutation = path.endswith("/git-receive-pack") or path.endswith("/rebuild-cache")
    if path.endswith("/info/refs"):
        is_mutation = str(request.query_params.get("service") or "") == "git-receive-pack"

    project_id = str(request.path_params.get("project_id") or "")
    auth: dict[str, Any] | None = None
    if not project_id:
        access_key = str(request.path_params.get("access_key") or "")
        if access_key:
            project_id, auth = await git_router.resolve_git_access_point(access_key, request)
    else:
        scope_id = str(request.path_params.get("scope_id") or "")
        auth = (
            await git_auth.resolve_git_scope_auth(project_id, scope_id, request)
            if scope_id
            else await git_auth.resolve_git_project_auth(project_id, request)
        )
    if not project_id:
        yield
        return
    runtime_grant = (auth or {}).get("_runtime_grant")
    if runtime_grant is None:
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=403,
            message="Git credential does not allow repository access",
        )
    if is_mutation and not bool(getattr(runtime_grant, "can_write", False)):
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=403,
            message="Git credential does not allow writes",
        )

    async with write_lease.ProjectWriteLease(project_id, f"git.{request.method.lower()}"):
        yield
