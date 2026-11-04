"""P-3 — Dashboard node-count cache.

The fix caches `compute_node_counts` keyed by (project_id, head_commit_id).
We assert: same head ⇒ tree walked once; new head ⇒ tree walked again;
cache misses don't raise; cache stays bounded.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.platform.dashboard.sections import (
    _NODE_COUNT_CACHE,
    compute_node_counts,
)


class _Entry:
    def __init__(self, etype: str):
        self.type = etype


def _ops(head_commit_returns: list[str], entries: list[_Entry]):
    """Build a ProductOperationAdapter stub. Each call to get_head_commit_id pops the next
    value from head_commit_returns; list_tree always returns `entries` and
    is observably counted."""
    ops = MagicMock()

    def _head(_pid):
        return head_commit_returns.pop(0)

    ops.get_head_commit_id.side_effect = _head
    ops.list_tree.return_value = entries
    return ops


def setup_function(_):
    _NODE_COUNT_CACHE.clear()


def test_repeat_request_same_commit_walks_tree_once():
    project_id = "proj-cache"
    ops = _ops(["commit-1", "commit-1"], [_Entry("folder"), _Entry("file")])

    a = compute_node_counts(ops, project_id)
    b = compute_node_counts(ops, project_id)

    assert (a.folders, a.files, a.total) == (1, 1, 2)
    assert (b.folders, b.files, b.total) == (1, 1, 2)
    # Fix's core property: list_tree called only ONCE despite two requests.
    assert ops.list_tree.call_count == 1


def test_new_commit_invalidates_cache():
    """When the project mutates (head_commit changes), the cache must miss
    and re-walk."""
    project_id = "proj-cache"
    ops = _ops(
        ["commit-1", "commit-2"],
        [_Entry("folder"), _Entry("file")],
    )
    compute_node_counts(ops, project_id)
    compute_node_counts(ops, project_id)
    assert ops.list_tree.call_count == 2


def test_head_commit_unavailable_falls_back_to_live():
    """If get_head_commit_id raises, we still return correct counts —
    just without caching."""
    ops = MagicMock()
    ops.get_head_commit_id.side_effect = RuntimeError("down")
    ops.list_tree.return_value = [_Entry("folder")]
    res = compute_node_counts(ops, "proj-x")
    assert res.folders == 1
    assert res.total == 1


def test_cache_bound_clears_when_full():
    """Per-process cache must be bounded — long-running workers should not
    OOM from a stream of distinct (project, commit) pairs."""
    from src.platform.dashboard.sections import _NODE_COUNT_CACHE_MAX
    # Pre-fill cache to its max.
    for i in range(_NODE_COUNT_CACHE_MAX):
        _NODE_COUNT_CACHE[(f"p-{i}", f"c-{i}")] = MagicMock()

    ops = _ops(["new-commit"], [_Entry("file")])
    compute_node_counts(ops, "new-project")
    # The cache was full → cleared, then this entry inserted.
    assert len(_NODE_COUNT_CACHE) == 1
    assert ("new-project", "new-commit") in _NODE_COUNT_CACHE
