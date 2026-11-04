"""Port through which storage asks its process to admit a Project write.

Storage must not know what a Project lifecycle is or how keys map to Projects,
and it must never write a Project's data without admission. Each process
installs the implementation at startup. A process that did not install one
refuses the write.
"""

from __future__ import annotations

from typing import Any, ContextManager, Protocol


class ProjectWriteAdmission(Protocol):
    def active_lease(self, project_id: str) -> Any | None:
        """Return the caller's live claim on this Project, if it has one."""

    def requires_lease(self, key: str) -> bool:
        """Return whether writing this object key needs a held lease."""

    def object_write(self, key: str, operation: str) -> ContextManager[Any]:
        """Hold admission for the duration of one physical object write."""


_installed: ProjectWriteAdmission | None = None


def install_project_write_admission(admission: ProjectWriteAdmission) -> None:
    global _installed
    _installed = admission


def project_write_admission() -> ProjectWriteAdmission:
    if _installed is None:
        raise RuntimeError("Project write admission is not installed in this process")
    return _installed
