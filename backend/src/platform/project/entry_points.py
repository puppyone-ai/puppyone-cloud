"""Port through which Project learns how many entry points it has.

An entry point belongs to the module that owns it, so Project never imports
that module. Each owner registers a counter, keyed by owner so that installing
twice cannot count twice, and a new kind of entry point only adds a registration.
The API process installs them at startup.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

EntryPointCounter = Callable[[list[str]], dict[str, int]]

_counters: dict[str, EntryPointCounter] = {}


def register_entry_point_counter(owner: str, counter: EntryPointCounter) -> None:
    _counters[owner] = counter


def count_entry_points(project_ids: list[str]) -> dict[str, int]:
    """Count user-created entry points, excluding built-in connection methods."""
    if not project_ids:
        return {}
    if not _counters:
        raise RuntimeError("Project entry point counters are not installed in this process")
    totals: Counter[str] = Counter()
    for counter in _counters.values():
        totals.update(counter(project_ids))
    return dict(totals)
