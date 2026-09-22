"""Parent/child path-scoped fanout (M4) — pure.

A publish to scope S touching some paths advances the project SoT. Instead of
eagerly force-pulling every overlapping scope's clients, we compute — per other
scope — exactly which of its paths changed (translated into that scope's
coordinates) and append a path-scoped upstream event for it (events.py). Each
scope's sidecars then integrate/hold lazily (decide_upstream). A scope whose
subtree doesn't intersect the change gets no event → its clients are never
disturbed.
"""

from __future__ import annotations


def _norm(p: str) -> str:
    return p.strip().strip("/")


def to_abs(scope_path: str, changed_paths: set[str]) -> set[str]:
    """Translate paths relative to ``scope_path`` into project-root-absolute paths."""
    base = _norm(scope_path)
    out: set[str] = set()
    for p in changed_paths:
        rel = _norm(p)
        out.add(f"{base}/{rel}" if base else rel)
    return out


def fanout_targets(
    abs_paths: set[str],
    scopes: list[tuple[str, str]],
) -> dict[str, list[str]]:
    """Given root-absolute changed paths and the project's scopes
    ``[(scope_id, scope_path), ...]``, return ``{scope_id: paths-in-that-scope}``
    for every scope whose subtree intersects the change.

    A scope at path P is affected by an absolute path A iff A is within P's
    subtree (A == P or A under P). The returned paths are P-relative (Project root,
    P == "", sees absolute paths as-is).
    """
    targets: dict[str, list[str]] = {}
    norm_abs = {_norm(a) for a in abs_paths}
    for scope_id, scope_path in scopes:
        p = _norm(scope_path)
        rel: set[str] = set()
        for a in norm_abs:
            if p == "":
                rel.add(a)                       # root sees everything
            elif a == p or a.startswith(p + "/"):
                rel.add(a[len(p):].lstrip("/"))  # under P → P-relative
        if rel:
            targets[scope_id] = sorted(rel)
    return targets
