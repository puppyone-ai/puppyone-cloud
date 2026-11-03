"""Logical tree size measured from a verified closure."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.version_engine.storage.publication import ClosureManifest


def logical_verified_tree_bytes(manifest: ClosureManifest, tree_oid: str) -> int:
    """Measure a verified native tree without expanding paths or loading blobs.

    Memoize subtree sizes, not visited paths: two edges to the same subtree
    still count twice. Only current-tree edges participate, never commit history
    or external gitlinks. The manifest must come from physical closure proof.
    """
    if manifest.resolve is not None:
        root = manifest.object(tree_oid)
        if root.kind != "tree" or type(root.logical_bytes) is not int:
            raise ValueError("logical billing requires a verified tree summary")
        return root.logical_bytes
    records = manifest.objects
    if tree_oid not in records or records[tree_oid].kind != "tree":
        raise ValueError("logical billing requires a verified tree")
    sizes: dict[str, int] = {}
    active: set[str] = set()
    stack = [(tree_oid, False)]
    while stack:
        oid, exiting = stack.pop()
        if oid in sizes:
            continue
        record = records.get(oid)
        if record is None or record.kind not in {"tree", "blob"}:
            raise ValueError("logical billing requires a complete typed tree closure")
        if record.kind == "blob":
            if type(record.size) is not int or record.size < 0:
                raise ValueError("invalid logical blob size")
            value = record.size
        elif exiting:
            value = sum(sizes[child] for child, _kind in record.edges)
            active.remove(oid)
        else:
            if oid in active:
                raise ValueError("logical tree cycle")
            active.add(oid)
            stack.append((oid, True))
            for child, kind in record.edges:
                if (
                    kind not in {"tree", "blob"}
                    or child not in records
                    or records[child].kind != kind
                ):
                    raise ValueError("logical billing requires a complete typed tree closure")
                stack.append((child, False))
            continue
        if value > 2**63 - 1:
            raise OverflowError("logical tree bytes exceed the usage counter range")
        sizes[oid] = value
    return sizes[tree_oid]
