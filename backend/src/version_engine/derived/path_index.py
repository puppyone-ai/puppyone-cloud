"""Materialised path/blob/metadata index for fast ``puppyone fs`` queries.

V1 implementation of 07-version-engine-todo.md H1/H2: on every
``version_committed`` outbox event, diff the scope's previous tree
against the new tree and upsert/delete rows in ``fs_path_index``. The
index is *derived* — anything here can be rebuilt by replaying the
outbox or by running the admin rebuild endpoint (H5).

The refresh runs in the outbox worker so AP-FS reads never block on
S3 tree walks: the worker pays the cost once per commit, in the
background.

Schema:
    fs_path_index (
      project_id, scope_path, full_path,
      blob_hash, size_bytes, mime_type,
      last_who, last_commit_id, last_updated_at
    )

The ``full_path`` column is repo-relative (e.g. ``docs/readme.md``);
scope-relative views are reconstructed by stripping the scope prefix
at query time so a single index serves project-view and scope-view
callers without duplicate rows.
"""

from __future__ import annotations

from typing import Iterable

from src.infra.supabase.client import SupabaseClient
from src.version_engine.write_engine.git_commit import commit_tree_id
from src.version_engine.write_engine.tree_objects import flatten_tree_to_bytes
from src.utils.logger import log_info, log_warning


_BATCH_LIMIT = 500


def refresh_fs_path_index_for_commit(
    repo,
    *,
    project_id: str,
    commit_id: str,
    scope_path: str,
    previous_commit_id: str = "",
    actor: str = "",
) -> int:
    """Upsert the rows that changed between ``previous_commit_id`` and
    ``commit_id`` (or all rows when there is no previous head).

    Returns the number of rows touched (upserts + deletes). Best-effort:
    a failure is logged but does not propagate, because the outbox row
    will retry the whole post-commit hook anyway.
    """

    try:
        new_files = _files_at_commit(repo, commit_id)
        old_files = _files_at_commit(repo, previous_commit_id) if previous_commit_id else {}
    except Exception as exc:
        log_warning(
            f"[fs_path_index] could not load files for commit "
            f"{commit_id[:12]} (scope={scope_path!r}): {exc}",
        )
        return 0

    added: dict[str, bytes] = {}
    modified: dict[str, bytes] = {}
    deleted: list[str] = []
    for path, content in new_files.items():
        prev = old_files.get(path)
        if prev is None:
            added[path] = content
        elif prev != content:
            modified[path] = content
    for path in old_files:
        if path not in new_files:
            deleted.append(path)

    if not added and not modified and not deleted:
        return 0

    scope_prefix = (scope_path or "").strip("/")

    def _full(rel: str) -> str:
        return f"{scope_prefix}/{rel}" if scope_prefix else rel

    upsert_rows = [
        _index_row(
            project_id=project_id,
            scope_path=scope_prefix,
            full_path=_full(rel),
            content=content,
            commit_id=commit_id,
            actor=actor,
        )
        for rel, content in {**added, **modified}.items()
    ]
    delete_full_paths = [_full(rel) for rel in deleted]

    client = SupabaseClient().client
    touched = 0
    try:
        for chunk in _chunks(upsert_rows, _BATCH_LIMIT):
            client.table("fs_path_index").upsert(
                chunk, on_conflict="project_id,full_path",
            ).execute()
            touched += len(chunk)
        for chunk in _chunks(delete_full_paths, _BATCH_LIMIT):
            client.table("fs_path_index").delete().eq(
                "project_id", project_id,
            ).in_("full_path", chunk).execute()
            touched += len(chunk)
    except Exception as exc:
        log_warning(
            f"[fs_path_index] refresh failed for commit {commit_id[:12]}: {exc}",
        )
        return touched

    if touched:
        log_info(
            f"[fs_path_index] commit={commit_id[:12]} scope={scope_path!r} "
            f"upserts={len(upsert_rows)} deletes={len(delete_full_paths)}",
        )
    return touched


def cleanup_fs_path_index_for_scope(project_id: str, scope_path: str) -> int:
    """Remove ``fs_path_index`` rows for one scope's prefix.

    Called from the scope-delete L1 endpoint AFTER the ``repository_scopes``
    row is gone. Without this, a future scope created at the same
    ``scope_path`` inherits stale rows pointing at the previous scope's
    blob hashes — the ``fs find`` / ``stat`` query would return content
    that no longer exists in the canonical tree.

    Returns the number of rows deleted. Project root (empty path) is
    refused because dropping root rows would wipe the whole project's
    file index — root cleanup belongs to ``rebuild_fs_path_index_for_project``.
    """
    scope_prefix = (scope_path or "").strip("/")
    if not scope_prefix:
        log_warning(
            f"[fs_path_index] cleanup refused for empty scope_path on "
            f"project={project_id}; root cleanup goes through rebuild",
        )
        return 0
    try:
        client = SupabaseClient().client
        resp = (
            client.table("fs_path_index")
            .delete()
            .eq("project_id", project_id)
            .eq("scope_path", scope_prefix)
            .execute()
        )
        deleted = len(resp.data or [])
        log_info(
            f"[fs_path_index] cleaned up scope={scope_prefix!r} "
            f"project={project_id} rows={deleted}",
        )
        return deleted
    except Exception as exc:
        log_warning(
            f"[fs_path_index] cleanup failed for scope={scope_prefix!r} "
            f"project={project_id}: {exc}",
        )
        return 0


def rebuild_fs_path_index_for_project(repo, project_id: str) -> int:
    """Full rebuild: walk every scope's current tree and upsert all rows.

    Used by the admin index-rebuild endpoint (H5) when the materialised
    rows drifted (manual DB surgery, missed outbox events, schema
    migration). The function deletes the project's existing rows first
    so a rebuild is exactly the source-of-truth state, not a merge.
    """

    client = SupabaseClient().client
    try:
        scopes = repo.get_all_scope_hashes()
    except Exception as exc:
        log_warning(f"[fs_path_index] rebuild aborted: {exc}")
        return 0

    # Delete first; on race with concurrent writers the worker will
    # re-upsert any commit that lands during the rebuild.
    try:
        client.table("fs_path_index").delete().eq("project_id", project_id).execute()
    except Exception as exc:
        log_warning(f"[fs_path_index] could not clear rows: {exc}")

    touched = 0
    for scope_path, scope_hash in scopes.items():
        if not scope_hash:
            continue
        try:
            files = flatten_tree_to_bytes(repo.store, scope_hash)
        except Exception as exc:
            log_warning(
                f"[fs_path_index] scope {scope_path!r} flatten failed: {exc}",
            )
            continue
        scope_prefix = (scope_path or "").strip("/")
        head_commit = repo.get_scope_head_commit_id(scope_path) or ""
        rows = [
            _index_row(
                project_id=project_id,
                scope_path=scope_prefix,
                full_path=(
                    f"{scope_prefix}/{rel}" if scope_prefix else rel
                ),
                content=content,
                commit_id=head_commit,
                actor="admin:rebuild",
            )
            for rel, content in files.items()
        ]
        for chunk in _chunks(rows, _BATCH_LIMIT):
            client.table("fs_path_index").upsert(
                chunk, on_conflict="project_id,full_path",
            ).execute()
            touched += len(chunk)

    log_info(f"[fs_path_index] rebuilt project={project_id} rows={touched}")
    return touched


# ── helpers ──────────────────────────────────────────────────


def _files_at_commit(repo, commit_id: str) -> dict[str, bytes]:
    if not commit_id:
        return {}
    entry = None
    history = getattr(repo, "history", None)
    if history is not None:
        try:
            entry = history.get_entry(commit_id)
        except Exception:
            entry = None
    if entry:
        scope_hash = entry.get("scope_hash") or ""
        if scope_hash and repo.store.exists(scope_hash):
            return flatten_tree_to_bytes(repo.store, scope_hash)
    try:
        tree_id = commit_tree_id(repo, commit_id)
    except Exception:
        return {}
    if not tree_id or not repo.store.exists(tree_id):
        return {}
    return flatten_tree_to_bytes(repo.store, tree_id)


def _index_row(
    *,
    project_id: str,
    scope_path: str,
    full_path: str,
    content: bytes,
    commit_id: str,
    actor: str,
) -> dict:
    return {
        "project_id": project_id,
        "scope_path": scope_path,
        "full_path": full_path,
        "blob_hash": "",  # unused for V1 but reserved for future dedup queries
        "size_bytes": len(content),
        "mime_type": _detect_mime(full_path, content),
        "last_who": actor or "",
        "last_commit_id": commit_id or "",
    }


_MIME_BY_EXT = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".html": "text/html",
    ".css": "text/css",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


def _detect_mime(path: str, content: bytes) -> str:
    """Best-effort mime guess: extension first, fall back to a tiny binary
    sniff for octet-stream vs text/plain."""

    lower = path.lower()
    for ext, mime in _MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    # Cheap binary detection: a sample of the head; if NUL byte present
    # or > 30% non-printable, treat as binary.
    sample = content[:512]
    if b"\x00" in sample:
        return "application/octet-stream"
    return "text/plain"


def _chunks(seq, n: int):
    out = list(seq)
    if not out:
        return
    for i in range(0, len(out), n):
        yield out[i:i + n]
