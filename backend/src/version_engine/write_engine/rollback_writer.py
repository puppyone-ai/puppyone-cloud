"""Rollback write path for L5."""

from __future__ import annotations

import asyncio

from src.exceptions import CasRetriesExhausted
from src.version_engine.domain.intents import RollbackIntent, TransactionResult
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager
from src.version_engine.write_engine.audit import (
    log_done as _log_done,
    now_iso as _now_iso,
)
from src.version_engine.write_engine.cas_backoff import cas_backoff
from src.version_engine.write_engine.git_commit import build_git_commit
from src.version_engine.write_engine.path_utils import normalize_path
from src.version_engine.write_engine.publisher import (
    publish_project_update as _publish_project_update,
)
from src.version_engine.write_engine.root_state import (
    get_project_root_state_for_write as _get_project_root_state_for_write,
    get_project_view_head as _get_project_view_head,
    get_scope_state as _get_scope_state,
    graft_scope_tree as _graft_scope_tree,
    scope_tree_hash_for_write as _scope_tree_hash_for_write,
)
from src.version_engine.write_engine.scope_view import (
    build_scope_view_commit as _build_scope_view_commit,
    git_safe_parent as _git_safe_parent,
)
from src.version_engine.write_engine.tree_access import (
    files_at_commit as _files_at_commit,
    tree_hash_at_commit as _tree_hash_at_commit,
)
from src.version_engine.write_engine.tree_objects import (
    build_tree_from_files,
    compute_changeset,
    flatten_tree_to_bytes,
)
from src.utils.logger import log_info


_MAX_CAS_ATTEMPTS = 5


class RollbackWriter:
    """Root-first rollback writer."""

    def __init__(self, repo_manager: VersionRepoManager):
        self._repos = repo_manager

    async def rollback_root_first(
        self,
        intent: RollbackIntent,
        started_ms: int,
    ) -> TransactionResult:
        repo = self._repos.get_server_repo(intent.project_id)
        scope_norm = normalize_path(intent.scope_path)
        target_commit_id = intent.target_commit_id
        if not target_commit_id:
            raise ValueError("target_commit_id is required")

        target_entry = repo.get_history_entry(target_commit_id)
        if target_entry:
            target_scope = normalize_path(target_entry.get("scope_path", ""))
            if target_scope != scope_norm:
                raise ValueError(
                    f"commit {target_commit_id} belongs to scope '{target_scope}', "
                    f"not '{scope_norm}'"
                )
        else:
            target_tree = _tree_hash_at_commit(repo, scope_norm, target_commit_id)
            if not target_tree:
                raise ValueError(f"commit {target_commit_id} not found")

        target_files = await asyncio.to_thread(
            _files_at_commit, repo, scope_norm, target_commit_id,
        )
        new_scope_hash = await asyncio.to_thread(
            build_tree_from_files, repo.store, target_files,
        )

        last_error: Exception | None = None
        for attempt in range(_MAX_CAS_ATTEMPTS):
            await cas_backoff(attempt)
            attempt_no = attempt + 1
            old_root_hash, base_root_hash = _get_project_root_state_for_write(repo)
            project_head_commit_id = _get_project_view_head(repo, old_root_hash)
            current_scope_hash = _scope_tree_hash_for_write(
                repo, base_root_hash, scope_norm,
            )
            _cached_scope_hash, current_scope_head_id = _get_scope_state(
                repo, scope_norm,
            )
            current_scope_head_id = (
                current_scope_head_id
                or (project_head_commit_id if not scope_norm else "")
            )
            if target_commit_id == current_scope_head_id:
                return TransactionResult(
                    status="already-at-commit",
                    commit_id=current_scope_head_id,
                    new_scope_hash=current_scope_hash,
                    is_noop=True,
                )

            current_files = await asyncio.to_thread(
                flatten_tree_to_bytes, repo.store, current_scope_hash,
            )
            changes = compute_changeset(scope_norm, current_files, target_files)
            if not changes and new_scope_hash == current_scope_hash:
                return TransactionResult(
                    status="ok",
                    commit_id=current_scope_head_id,
                    new_scope_hash=current_scope_hash,
                    is_noop=True,
                )

            new_root_hash = _graft_scope_tree(
                repo, base_root_hash, scope_norm, new_scope_hash,
            )
            created_at_iso = _now_iso()
            commit_id = await asyncio.to_thread(
                build_git_commit,
                repo,
                tree_sha=new_root_hash,
                parent_sha=_git_safe_parent(repo, project_head_commit_id),
                who=intent.actor,
                message=intent.message or f"rollback to {target_commit_id}",
                created_at_iso=created_at_iso,
                validate_parent_graph=False,
            )
            scope_head_commit_id = ""
            if scope_norm:
                scope_head_commit_id = await asyncio.to_thread(
                    _build_scope_view_commit,
                    repo,
                    scope_path=scope_norm,
                    scope_hash=new_scope_hash,
                    parent_id=current_scope_head_id,
                    actor=intent.actor,
                    message=intent.message or f"rollback to {target_commit_id}",
                    created_at_iso=created_at_iso,
                    source_channel=intent.source_channel,
                    original_commit_id=target_commit_id,
                    base_commit_id=current_scope_head_id,
                )

            result = await _publish_project_update(
                repo_manager=self._repos,
                repo=repo,
                project_id=intent.project_id,
                old_root_hash=old_root_hash,
                new_root_hash=new_root_hash,
                scope_path=scope_norm,
                scope_hash=new_scope_hash,
                scope_head_commit_id=scope_head_commit_id,
                expected_scope_head_commit_id=(
                    current_scope_head_id if scope_norm else None
                ),
                commit_id=commit_id,
                actor=intent.actor,
                message=intent.message or f"rollback to #{target_commit_id}",
                op_type="rollback",
                audit_detail={
                    "target_commit_id": target_commit_id,
                    "new_commit_id": commit_id,
                    **intent.audit_detail,
                },
                changes=changes,
                conflicts=None,
                created_at_iso=created_at_iso,
                cas_attempt=attempt_no,
                merged=False,
                merged_changes=[],
                source_channel=intent.source_channel,
                base_commit_id=current_scope_head_id,
                proposed_tree_id=new_scope_hash,
                intent_type="rollback",
            )
            if result is not None:
                result.status = "rolled-back"
                result.new_scope_hash = new_scope_hash
                _log_done("rollback", intent.project_id, scope_norm, result, started_ms)
                return result

            last_error = RuntimeError("project root CAS lost")
            log_info(
                f"[version_engine][rollback] root CAS lost "
                f"(attempt {attempt_no}/{_MAX_CAS_ATTEMPTS}) "
                f"project={intent.project_id} scope={scope_norm!r}",
            )

        await asyncio.to_thread(
            repo.record_audit,
            "rollback_error",
            intent.actor,
            {
                "scope": scope_norm,
                "target_commit_id": target_commit_id,
                "error": "root CAS failed after max retries",
                **(intent.audit_detail or {}),
            },
        )
        raise CasRetriesExhausted(
            f"[version_engine][rollback] root CAS still failing "
            f"after {_MAX_CAS_ATTEMPTS} attempts "
            f"(project={intent.project_id}, scope={scope_norm!r}); "
            f"last error: {last_error}",
        )
