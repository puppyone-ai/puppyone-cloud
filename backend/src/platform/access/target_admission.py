"""Admitted Version Engine target facts."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.platform.access.channel_pause import enforce_channel_pause
from src.platform.access.permission import (
    ensure_repo_readable,
    ensure_repo_writable,
)
from src.version_engine.admission.repo_facade import RepoFacade


_READ_ACTIONS = frozenset({"read", "ls", "cat", "stat", "tree", "history", "diff"})
_WRITE_ACTIONS = frozenset({"write", "delete", "move", "copy", "mkdir", "rmdir", "bulk_write", "touch", "resolve"})


@dataclass(frozen=True)
class TargetAdmission:
    """L3 output consumed by adapters and the write engine boundary.

    A target admission is not merely an auth result. It captures the concrete
    project/scope/ref/actions snapshot that a caller is allowed to operate on.

    Construct via ``admit_target`` rather than direct instantiation so
    every entry point goes through the same gate: mode check, channel
    pause, and audit-detail capture.
    """

    project_id: str
    actor: str
    source_channel: str
    scope_path: str = ""
    ref: str = "refs/heads/main"
    mode: str = "rw"
    excludes: tuple[str, ...] = ()
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    audit_detail: dict = field(default_factory=dict)

    def allows(self, action: str) -> bool:
        return action in self.allowed_actions


def admit_target(
    auth: dict,
    facade: RepoFacade,
    *,
    action: str,
    source_channel: str,
    channel_header: str | None = None,
    log_prefix: str = "[Admission]",
) -> TargetAdmission:
    """Run all L3 checks for one entry point and return the admission.

    Order matters: cheap mode check first (denies viewers without any
    DB round-trip), then channel pause (one cached connector lookup).
    The audit doc says "L3 is the permission decision point ... mode,
    excludes, connector status, ref policy, and audit identity" — this
    function is the single place that runs them all and emits the
    contract object the rest of the engine consumes.
    """
    if action in _WRITE_ACTIONS:
        ensure_repo_writable(facade)
        allowed = _WRITE_ACTIONS | _READ_ACTIONS  # write implies read
    elif action in _READ_ACTIONS:
        ensure_repo_readable(facade)
        allowed = _READ_ACTIONS
    else:
        # Unknown action — refuse rather than guess. Adding a new action
        # kind must be deliberate.
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"Admission for unknown action: {action!r}",
        )

    enforce_channel_pause(auth, channel_header, log_prefix=log_prefix)

    return TargetAdmission(
        project_id=facade.project_id,
        actor=str(auth.get("agent") or ""),
        source_channel=source_channel,
        scope_path=facade.scope_path,
        ref=facade.ref,
        mode=facade.mode,
        excludes=facade.excludes,
        allowed_actions=allowed,
        audit_detail=facade.audit_detail(),
    )
