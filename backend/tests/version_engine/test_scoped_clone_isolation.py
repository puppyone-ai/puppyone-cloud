"""E2: scoped Git clones must not leak sibling-scope objects.

The contract: when a client clones via a scoped Git URL, the bare repo
built for ``git upload-pack`` only contains blobs/trees/commits
reachable from the scope-filtered head — sibling-scope content is
absent from the object set entirely, so even a hand-crafted ``git
fetch <sibling-sha>`` returns 404.

This is the pure-Python version of the contract: we exercise the
reachability walk that decides which objects get copied into the bare
repo. The real-git-CLI counterpart lives in
``test_real_git_cli_scoped_remote_*`` and is skipped on Windows.
"""

from __future__ import annotations

from src.version_engine.derived.object_gc import mark_reachable_objects
from src.version_engine.adapters.git.view_projection import git_view_head_commit
from src.version_engine.write_engine.git_commit import build_git_commit
from src.version_engine.write_engine.tree_objects import build_tree_from_files


def _publish_initial_root(server_repo, files: dict[str, bytes]) -> tuple[str, str]:
    """Build a root tree + a commit for it and CAS it into the root scope.

    Returns ``(tree_id, commit_id)``. We use the engine's own helpers so
    the resulting graph is exactly what receive-pack would have produced.
    """

    tree_id = build_tree_from_files(server_repo.store, files)
    commit_id = build_git_commit(
        server_repo,
        tree_sha=tree_id,
        parent_sha="",
        who="git:seed",
        message="seed",
        created_at_iso="2026-05-16T00:00:00Z",
    )
    # Bypass the engine here — we just need the root scope head pointed
    # at the commit so git_view_head_commit can read it.
    server_repo.cas_update_scope("", "", tree_id, head_commit_id=commit_id)
    server_repo.set_head_commit_id(commit_id)
    return tree_id, commit_id


def _blob_id(store, content: bytes) -> str:
    """Re-hash a blob deterministically without re-storing it."""
    return store.put_blob(content)


# ── reachability under scope filtering ────────────────────────────


def test_root_view_reachability_includes_every_blob(server_repo):
    """Sanity check: an unscoped view of the root commit reaches every
    blob in the tree."""
    blob_a = _blob_id(server_repo.store, b"hello docs\n")
    blob_k = _blob_id(server_repo.store, b"secret key material\n")
    _publish_initial_root(server_repo, {
        "docs/a.md": b"hello docs\n",
        "secret/key.txt": b"secret key material\n",
    })

    head = git_view_head_commit(server_repo, "", scope_excludes=None)
    assert head, "root view must have a head commit"

    reachable = mark_reachable_objects(server_repo, [head])
    assert blob_a in reachable
    assert blob_k in reachable


def test_scoped_view_does_not_reach_sibling_blobs(server_repo):
    """A scoped view of ``docs/`` must NOT expose ``secret/key.txt``."""
    server_repo.add_scope("docs", "docs")
    server_repo.add_scope("secret", "secret")
    docs_blob = _blob_id(server_repo.store, b"hello docs\n")
    secret_blob = _blob_id(server_repo.store, b"secret key material\n")
    _publish_initial_root(server_repo, {
        "docs/a.md": b"hello docs\n",
        "docs/b.md": b"more docs\n",
        "secret/key.txt": b"secret key material\n",
    })

    # Build a scope tree for "docs" — same shape as what real writes
    # would land in the scope-state table.
    docs_tree = build_tree_from_files(server_repo.store, {
        "a.md": b"hello docs\n",
        "b.md": b"more docs\n",
    })
    docs_commit = build_git_commit(
        server_repo,
        tree_sha=docs_tree,
        parent_sha="",
        who="git:docs",
        message="docs head",
        created_at_iso="2026-05-16T00:00:00Z",
    )
    server_repo.cas_update_scope("docs", "", docs_tree, head_commit_id=docs_commit)

    head = git_view_head_commit(server_repo, "docs", scope_excludes=None)
    assert head, "scoped view must have a head"

    reachable = mark_reachable_objects(server_repo, [head])
    assert docs_blob in reachable, "scope view must include in-scope blob"
    # The contract under audit:
    assert secret_blob not in reachable, (
        "scope view leaked a sibling-scope blob; this would let a scoped "
        "client `git fetch <sha>` an out-of-scope object"
    )


def test_scoped_view_with_excludes_strips_excluded_subtree(server_repo):
    """An access point with ``exclude=['/docs/private/']`` must drop those
    blobs from the reachable set even though they live in the scope.

    Excludes are stored as full repository-relative paths (the access
    point config uses ``"/docs/private/"`` form). The scoped view filter
    normalises both sides before comparing, so a leading/trailing slash
    is tolerated but the path is NOT scope-relative.
    """
    public_blob = _blob_id(server_repo.store, b"public\n")
    private_blob = _blob_id(server_repo.store, b"private notes\n")
    _publish_initial_root(server_repo, {
        "docs/public.md": b"public\n",
        "docs/private/notes.md": b"private notes\n",
    })

    # Build the docs scope tree containing both subdirectories.
    server_repo.add_scope("docs", "docs", exclude=["/docs/private/"])
    docs_tree = build_tree_from_files(server_repo.store, {
        "public.md": b"public\n",
        "private/notes.md": b"private notes\n",
    })
    docs_commit = build_git_commit(
        server_repo,
        tree_sha=docs_tree,
        parent_sha="",
        who="git:docs",
        message="docs",
        created_at_iso="2026-05-16T00:00:00Z",
    )
    server_repo.cas_update_scope("docs", "", docs_tree, head_commit_id=docs_commit)

    head = git_view_head_commit(
        server_repo, "docs", scope_excludes=["/docs/private/"],
    )
    assert head

    reachable = mark_reachable_objects(server_repo, [head])
    assert public_blob in reachable
    assert private_blob not in reachable, (
        "exclude pattern was not applied to the scoped clone view"
    )


def test_empty_scope_view_is_safe(server_repo):
    """A scope that has never been written to either returns ``""`` (no
    head) or a fresh empty-tree commit — never a leak."""
    server_repo.add_scope("nothing", "nothing")
    _publish_initial_root(server_repo, {
        "other/file.md": b"unrelated\n",
    })

    head = git_view_head_commit(server_repo, "nothing", scope_excludes=None)
    # Either no head (preferred) or a commit with the empty tree id.
    if head:
        reachable = mark_reachable_objects(server_repo, [head])
        # All reachable objects must be the empty tree + commit objects,
        # never a sibling blob.
        for object_id in reachable:
            obj_type, _ = server_repo.store.get_object(object_id)
            assert obj_type in {"commit", "tree"}, (
                f"empty scope view should never reach a blob, got "
                f"{obj_type} {object_id[:8]}"
            )
