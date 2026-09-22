from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.platform.authorization.models import ProjectGrant
from src.platform.project.models import Project
from src.platform.repository_target.models import RepositoryTarget


class GitCredentialMode(StrEnum):
    READ = "r"
    READ_WRITE = "rw"


@dataclass(frozen=True, slots=True)
class RepositoryProjectContext:
    """Secret-free human UI context for one authorized repository target."""

    project: Project
    grant: ProjectGrant
    target: RepositoryTarget
    scope_path: str | None


@dataclass(frozen=True, slots=True)
class IssuedGitCredential:
    """Hash-only issuance acknowledgement for one exact repository target."""

    credential_id: str
    target: RepositoryTarget
    mode: GitCredentialMode
    replayed: bool = False
