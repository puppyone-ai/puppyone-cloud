"""Recover project roots that reference a missing Git tree object.

The normal damaged-folder repair tool needs a readable project root.  This
tool handles the earlier failure mode: ``projects.version_root_hash`` itself
is missing from the object store.  It searches newest-first through recorded
project roots, and can only restore a root that still decodes as a Git tree.

Usage (inside the deployed backend environment, or with its DB/S3 variables):

    python -m scripts.repair_missing_project_roots --all
    python -m scripts.repair_missing_project_roots --project-id <id> --apply

``--apply`` is deliberately rejected with ``--all``.  Operators must review
the dry-run result and name the exact recoverable projects to mutate.  Every
write is a CAS from the observed damaged root and emits an audit-log event.
Projects with no readable historical root are reported but never changed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from src.infra.supabase.client import SupabaseClient
from src.version_engine.bootstrap.dependencies import (
    build_worker_version_engine_container,
)


INCIDENTS_TABLE = "version_project_root_integrity_incidents"
_CANONICAL_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RootRecoveryPlan:
    project_id: str
    current_root: str
    current_root_healthy: bool = False
    current_root_scope_hash: str = ""
    recovery_root: str = ""
    source_commit_id: str = ""
    source_created_at: str = ""

    @property
    def status(self) -> str:
        if not self.current_root:
            return "empty"
        if self.current_root_healthy:
            return "healthy" if self.current_root_scope_hash == self.current_root else "cache_stale"
        return "recoverable" if self.recovery_root else "unrecoverable"


def _is_readable_tree(store, object_id: str) -> bool:
    if not object_id:
        return False
    try:
        object_type, _body = store.get_object(object_id)
    except Exception:
        return False
    return object_type == "tree"


def plan_project_root_recovery(repo, *, history_limit: int = 100) -> RootRecoveryPlan:
    """Return a no-write repair plan for one project's current root."""

    project_id = str(getattr(repo, "_project_id", ""))
    current_root = str(repo.get_root_hash() or "")
    current_root_scope_hash = ""
    get_scope_state = getattr(repo, "get_scope_state", None)
    if callable(get_scope_state):
        current_root_scope_hash, _head = get_scope_state("")
        current_root_scope_hash = str(current_root_scope_hash or "")
    if not current_root:
        return RootRecoveryPlan(project_id=project_id, current_root=current_root)

    # get_history_since() returns ascending order; walk backwards so a repair
    # restores the most recent readable state, never an arbitrary old one.
    entries = repo.get_history_since("", None, history_limit)
    if _is_readable_tree(repo.store, current_root):
        source = next(
            (
                entry
                for entry in reversed(entries)
                if str(entry.get("root_hash") or entry.get("root") or "") == current_root
            ),
            {},
        )
        return RootRecoveryPlan(
            project_id=project_id,
            current_root=current_root,
            current_root_healthy=True,
            current_root_scope_hash=current_root_scope_hash,
            recovery_root=current_root,
            source_commit_id=str(source.get("commit_id") or ""),
            source_created_at=str(source.get("created_at") or ""),
        )
    for entry in reversed(entries):
        root_hash = str(entry.get("root_hash") or entry.get("root") or "")
        if root_hash and root_hash != current_root and _is_readable_tree(repo.store, root_hash):
            return RootRecoveryPlan(
                project_id=project_id,
                current_root=current_root,
                current_root_scope_hash=current_root_scope_hash,
                recovery_root=root_hash,
                source_commit_id=str(entry.get("commit_id") or ""),
                source_created_at=str(entry.get("created_at") or ""),
            )
    return RootRecoveryPlan(
        project_id=project_id,
        current_root=current_root,
        current_root_scope_hash=current_root_scope_hash,
    )


def apply_project_root_recovery(repo, plan: RootRecoveryPlan) -> bool:
    """CAS-restore one reviewed recovery plan and record its audit evidence."""

    if plan.status not in {"recoverable", "cache_stale"}:
        return False
    if str(getattr(repo, "_project_id", "")) != plan.project_id:
        raise ValueError("recovery plan does not belong to the supplied repository")
    if not _is_readable_tree(repo.store, plan.recovery_root):
        raise RuntimeError("recovery root is no longer readable; refusing to update")

    # The old value is part of the CAS condition, so a concurrent user write
    # wins instead of being overwritten by an operator recovery.
    root_changed = plan.status == "recoverable"
    if root_changed and not repo.cas_update_root_hash(plan.current_root, plan.recovery_root):
        return False

    # The root-scope hash is a derived cache but write and protocol paths still
    # consult it.  Align it with the recovered canonical root using its own
    # CAS; a concurrent scope write wins and leaves a clear false result for an
    # operator to re-plan rather than silently overwriting that state.
    cas_scope = getattr(repo, "cas_update_scope", None)
    if callable(cas_scope) and not cas_scope(
        "", plan.current_root_scope_hash, plan.recovery_root, plan.source_commit_id
    ):
        return False

    if root_changed or plan.status == "cache_stale":
        repo.record_audit(
            "version_root_recovered_from_history",
            "system:root-integrity-repair",
            {
                "previous_root": plan.current_root,
                "recovered_root": plan.recovery_root,
                "source_commit_id": plan.source_commit_id,
                "source_created_at": plan.source_created_at,
                "root_changed": root_changed,
                "root_scope_cache_aligned": True,
            },
        )
    return True


def mark_project_root_irrecoverable(client, plan: RootRecoveryPlan) -> bool:
    """Persist an incident for a reviewed, genuinely unrecoverable root.

    The root hash remains untouched.  Re-running the scan updates only the
    observation timestamp/reason and is therefore safe for operations jobs.
    """

    if plan.status != "unrecoverable" or not plan.current_root:
        raise ValueError("only an unrecoverable non-empty root can be marked")
    # Legacy 16-hex roots are intentionally unsupported by the current Git
    # object namespace.  Do not weaken the canonical-ID database constraint
    # just to persist diagnostic metadata; their unrecoverable status is
    # detected directly by the read API.
    if not _CANONICAL_OBJECT_ID_RE.fullmatch(plan.current_root):
        return False
    client.table(INCIDENTS_TABLE).upsert(
        {
            "project_id": plan.project_id,
            "root_hash": plan.current_root,
            "status": "irrecoverable",
            "reason": "current root object missing and no readable historical root tree",
            "last_detected_at": datetime.now(timezone.utc).isoformat(),
            "marked_by": "system:root-integrity-repair",
        },
        on_conflict="project_id",
    ).execute()
    return True


def _project_ids(client, explicit_ids: list[str], *, scan_all: bool) -> list[str]:
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))
    if not scan_all:
        raise ValueError("provide --project-id or use --all for a dry-run scan")
    rows = (
        client.table("projects")
        .select("id")
        .not_.is_("version_root_hash", "null")
        .neq("version_root_hash", "")
        .limit(1000)
        .execute()
        .data
        or []
    )
    return [str(row["id"]) for row in rows if row.get("id")]


def run(
    *, project_ids: list[str], apply: bool, mark_unrecoverable: bool = False,
    client=None,
) -> tuple[list[RootRecoveryPlan], int]:
    """Plan or apply recoveries.  Returns (plans, failed_apply_count)."""

    repos = build_worker_version_engine_container().repo_manager
    db = client or SupabaseClient().client
    plans: list[RootRecoveryPlan] = []
    failed = 0
    for project_id in project_ids:
        repo = repos.get_server_repo(project_id)
        plan = plan_project_root_recovery(repo)
        plans.append(plan)
        source = (
            f" source_commit={plan.source_commit_id[:12]}"
            f" source_at={plan.source_created_at}"
            if plan.recovery_root
            else ""
        )
        print(
            f"project={plan.project_id} status={plan.status} "
            f"current_root={plan.current_root[:12] or '<empty>'}"
            f"{source}"
        )
        if apply and plan.status in {"recoverable", "cache_stale"}:
            applied = apply_project_root_recovery(repo, plan)
            print(f"project={plan.project_id} applied={applied}")
            if not applied:
                failed += 1
        if mark_unrecoverable and plan.status == "unrecoverable":
            marked = mark_project_root_irrecoverable(db, plan)
            outcome = "true" if marked else "legacy_root_not_recorded"
            print(f"project={plan.project_id} irrecoverable_marked={outcome}")
    return plans, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", action="append", default=[])
    parser.add_argument("--all", action="store_true", help="dry-run all projects")
    parser.add_argument("--apply", action="store_true", help="apply reviewed project IDs")
    parser.add_argument(
        "--mark-unrecoverable",
        action="store_true",
        help="persist incidents for reviewed unrecoverable roots",
    )
    args = parser.parse_args(argv)

    if args.apply and args.all:
        parser.error("--apply requires explicit --project-id values; --all is dry-run only")
    try:
        client = SupabaseClient().client
        project_ids = _project_ids(client, args.project_id, scan_all=args.all)
    except ValueError as exc:
        parser.error(str(exc))

    plans, failed = run(
        project_ids=project_ids,
        apply=args.apply,
        mark_unrecoverable=args.mark_unrecoverable,
        client=client,
    )
    counts = {status: sum(plan.status == status for plan in plans) for status in (
        "healthy", "cache_stale", "recoverable", "unrecoverable", "empty"
    )}
    print(
        "summary "
        f"healthy={counts['healthy']} "
        f"cache_stale={counts['cache_stale']} "
        f"recoverable={counts['recoverable']} "
        f"unrecoverable={counts['unrecoverable']} empty={counts['empty']}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
