"""Port through which storage asks its process to admit a Project write.

Storage must not know what a Project lifecycle is, and it must never write a
Project's data without admission. Each process installs the implementation at
startup. A process that did not install one refuses the write.
"""

from __future__ import annotations

from typing import Any, ContextManager, Protocol


class ProjectWriteAdmission(Protocol):
    def active_lease(self, project_id: str) -> Any | None:
        """Return the caller's live claim on this Project, if it has one."""

    def object_write(self, project_id: str, operation: str) -> ContextManager[Any]:
        """Hold admission for the duration of one physical object write."""


_installed: ProjectWriteAdmission | None = None


def install_project_write_admission(admission: ProjectWriteAdmission) -> None:
    global _installed
    _installed = admission


def project_write_admission() -> ProjectWriteAdmission:
    if _installed is None:
        raise RuntimeError("Project write admission is not installed in this process")
    return _installed
