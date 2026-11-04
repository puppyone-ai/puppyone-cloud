"""Diagnose and repair "Damaged folder" dangling subtrees for a project.

A "Damaged folder" is a tree entry whose subtree object is missing from the
object store while a parent tree still references it (see
``project_damaged_folder_dangling_tree``). This tool runs IN the backend
context (where the object store is reachable), so it can do what the read-only
HTTP API cannot: tell whether each damaged folder is actually RECOVERABLE.

For every damaged folder it searches for a fully-present replacement subtree:
  1. a per-scope ``scope_hash`` whose path matches the folder exactly, then
  2. any historical project root that resolves the folder's path cleanly.
If one is found the folder is recoverable (tier ①) — ``--apply`` re-grafts it
into the live root via a single CAS. If none is found the folder's content is
genuinely gone (tier ②) and the only cleanup is to delete the dead entry.

Usage (run where DB + object store env is configured, e.g. the prod backend):

    python -m scripts.repair_damaged_folders <project_id>            # dry-run
    python -m scripts.repair_damaged_folders <project_id> --apply    # recover

Dry-run is read-only. ``--apply`` only ever GRAFTS recovered subtrees back in
(never deletes), so it cannot lose data; unrecoverable folders are left as-is
and reported for manual deletion.
"""

from __future__ import annotations

import sys

from src.platform.project.write_lease import install_storage_write_admission
from src.version_engine.bootstrap.dependencies import (
    build_worker_version_engine_container,
)
from src.version_engine.derived.projection import graft_subtree
from src.version_engine.write_engine.git_object_format import decode_tree
from src.version_engine.write_engine.tree_objects import find_missing_tree_objects


def _navigate(store, root_hash: str, path: str) -> str | None:
    """Subtree hash at ``path`` under ``root_hash``, or None if any segment or
    object along the way is missing/unreadable/not-a-tree."""
    current = root_hash
    for part in [p for p in path.split("/") if p]:
        if not current or not store.exists(current):
            return None
        try:
            obj_type, body = store.get_object(current)
        except Exception:
            return None
        if obj_type != "tree":
            return None
        nxt: str | None = None
        for entry in decode_tree(body):
            if entry.name == part:
                nxt = entry.sha1_hex if entry.is_dir else None
                break
        if not nxt:
            return None
        current = nxt
    return current


def scan_damaged_dirs(store, root_hash: str) -> list[str]:
    """Return the paths of folders whose subtree object is missing."""
    if not root_hash:
        return []
    if not store.exists(root_hash):
        return ["<project root>"]
    damaged: list[str] = []
    stack: list[tuple[str, str]] = [("", root_hash)]
    while stack:
        path, tree_hash = stack.pop()
        try:
            obj_type, body = store.get_object(tree_hash)
        except Exception:
            continue
        if obj_type != "tree":
            continue
        for entry in decode_tree(body):
            if not entry.is_dir:
                continue
            child_path = f"{path}/{entry.name}" if path else entry.name
            if not store.exists(entry.sha1_hex):
                damaged.append(child_path)  # don't recurse into a missing tree
            else:
                stack.append((child_path, entry.sha1_hex))
    return damaged


def find_recovery_subtree(store, repo, path: str) -> tuple[str | None, str]:
    """Find a fully-present subtree that can replace the damaged folder."""
    norm = path.strip("/")

    # 1) a scope whose tree IS exactly this folder (sub-scope case).
    try:
        for scope_path, scope_hash in (repo.get_all_scope_hashes() or {}).items():
            if (scope_path or "").strip("/") != norm or not scope_hash:
                continue
            if store.exists(scope_hash) and not find_missing_tree_objects(store, scope_hash):
                return scope_hash, f"scope_hash[{scope_path!r}]"
    except Exception:
        pass

    # 2) any historical project root that resolves this path cleanly.
    seen: set[str] = set()
    try:
        history = repo.get_history_since("", None, 0)
    except Exception:
        history = []
    for entry in history:
        for root_hash in (entry.get("root_hash"), entry.get("root")):
            if not root_hash or root_hash in seen:
                continue
            seen.add(root_hash)
            sub = _navigate(store, root_hash, path)
            if sub and not find_missing_tree_objects(store, sub):
                return sub, f"history_root[{root_hash[:12]}]"
    return None, ""


def _resolve_root(repo) -> str:
    for getter in (getattr(repo, "get_root_hash", None),
                   getattr(getattr(repo, "history", None), "get_root_hash", None)):
        if callable(getter):
            try:
                root = getter() or ""
            except Exception:
                root = ""
            if root:
                return root
    return ""


def repair_repo(repo, *, apply: bool) -> int:
    """Scan + (optionally) repair one already-loaded repo. Returns an exit code.

    Split out from ``repair_project`` so the recovery logic is unit-testable
    against a fake repo without building the worker container.
    """
    store = repo.store
    root = _resolve_root(repo)

    print(f"project={getattr(repo, '_project_id', '?')} root={root[:12] or '<none>'}")
    damaged = scan_damaged_dirs(store, root)
    if not damaged:
        print("No damaged folders found. Nothing to do.")
        return 0

    print(f"Damaged folders: {len(damaged)}")
    recovered: dict[str, str] = {}
    unrecoverable: list[str] = []
    for path in damaged:
        sub, source = find_recovery_subtree(store, repo, path)
        if sub:
            recovered[path] = sub
            print(f"  [RECOVERABLE] {path}  <- {source} ({sub[:12]})")
        else:
            unrecoverable.append(path)
            print(f"  [GONE]        {path}  (no surviving subtree/blobs — tier ②)")

    if not apply:
        print(
            f"\nDRY RUN. recoverable={len(recovered)} gone={len(unrecoverable)}. "
            "Re-run with --apply to re-graft the recoverable ones."
        )
        if unrecoverable:
            print("Unrecoverable folders must be deleted manually to clear the view:")
            for path in unrecoverable:
                print(f"  - {path}")
        return 0

    if not recovered:
        print("\nNothing recoverable to apply.")
        return 0

    new_root = root
    for path, sub in recovered.items():
        new_root = graft_subtree(store, new_root, path, sub)
    if new_root == root:
        print("\nGrafted root is unchanged; nothing to commit.")
        return 0
    ok = repo.cas_update_root_hash(root, new_root)
    print(
        f"\nAPPLIED: re-grafted {len(recovered)} folder(s). "
        f"root {root[:12]} -> {new_root[:12]} cas_ok={ok}"
    )
    if not ok:
        print("CAS lost (root advanced concurrently). Re-run to retry.")
        return 1
    if unrecoverable:
        print("Still unrecoverable (delete manually):")
        for path in unrecoverable:
            print(f"  - {path}")
    return 0


def repair_project(project_id: str, *, apply: bool) -> int:
    repos = build_worker_version_engine_container().repo_manager
    repo = repos.get_server_repo(project_id)
    return repair_repo(repo, apply=apply)


def main(argv: list[str]) -> int:
    install_storage_write_admission()
    args = [a for a in argv[1:] if a != "--apply"]
    apply = "--apply" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    return repair_project(args[0], apply=apply)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
