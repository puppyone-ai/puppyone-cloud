"""Permission checks for Version Engine write targets."""

from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException

from src.exceptions import ErrorCode, NotFoundException
from src.version_engine.admission.repo_facade import RepoFacade
from src.version_engine.domain.intents import ProjectWriteState


class ProjectWriteStateProvider(Protocol):
    def get_project_write_state(self, project_id: str, user_id: str) -> ProjectWriteState | None:
        ...


_READ_MODES = frozenset({"r", "rw", "read", "write"})
_WRITE_MODES = frozenset({"rw", "write", "w"})


def require_project_write_state(
    ops: ProjectWriteStateProvider,
    project_id: str,
    user_id: str,
) -> ProjectWriteState:
    """Authorize a Product/Web root write and return its CAS snapshot."""

    write_state = ops.get_project_write_state(project_id, user_id)
    if write_state is None or not write_state.role:
        raise NotFoundException(
            f"Project not found: {project_id}",
            code=ErrorCode.NOT_FOUND,
        )
    if not write_state.can_write:
        raise HTTPException(
            status_code=403,
            detail="Viewers cannot perform write operations",
        )
    return write_state


def is_mode_readable(mode: str) -> bool:
    return str(mode).lower() in _READ_MODES


def is_mode_writable(mode: str) -> bool:
    return str(mode).lower() in _WRITE_MODES


def ensure_mode_readable(mode: str) -> None:
    if not is_mode_readable(mode):
        raise HTTPException(status_code=403, detail="Access point does not allow file reads")


def ensure_mode_writable(mode: str) -> None:
    if not is_mode_writable(mode):
        raise HTTPException(status_code=403, detail="Access point is read-only")


def ensure_repo_readable(facade: RepoFacade) -> None:
    ensure_mode_readable(facade.mode)


def ensure_repo_writable(facade: RepoFacade) -> None:
    ensure_mode_writable(facade.mode)
