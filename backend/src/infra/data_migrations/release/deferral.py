"""Reviewed, per-environment deferral of historical data phases.

The release plan lists every data transformation an environment may need. An
environment that deliberately adopted a different, bounded rollout (Qubits'
per-project selections) declares the whole-environment phases it has not taken
in ``supabase/releases/deferred-data.json``. The declaration is versioned with
the source, so it is reviewed like code and immutable for a release SHA.

A deferred phase is never executed, skipped-with-receipt or reported as done:
the coordinator leaves it pending and reports it. Pending SQL that declares a
deferred artifact as its prerequisite still fails the release. Production may
not defer anything; it always runs the complete plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .plan import ReleasePlan

PATH = "supabase/releases/deferred-data.json"
DEFERRABLE_ENVIRONMENTS = frozenset({"staging"})


def load_deferred_phases(root: Path, environment: str | None, plan: ReleasePlan) -> frozenset[str]:
    if environment is None:
        return frozenset()
    path = root / PATH
    if not path.exists():
        return frozenset()
    document = json.loads(path.read_text())
    if set(document) != {"format_version", "environments"} or document["format_version"] != 1:
        raise ValueError("Invalid deferred data declaration")
    environments = document["environments"]
    if not isinstance(environments, dict) or not set(environments) <= DEFERRABLE_ENVIRONMENTS:
        raise ValueError("Only staging may defer historical data phases")
    entry = environments.get(environment)
    if entry is None:
        return frozenset()
    if (
        not isinstance(entry, dict)
        or set(entry) != {"phases", "reason"}
        or not isinstance(entry["reason"], str)
        or not entry["reason"].strip()
        or not isinstance(entry["phases"], list)
        or len(set(entry["phases"])) != len(entry["phases"])
    ):
        raise ValueError("Deferred data phases need a unique list and a reason")
    data_phases = {phase.id for phase in plan.phases if phase.kind == "data"}
    deferred = frozenset(entry["phases"])
    if not deferred <= data_phases:
        raise ValueError("Only data phases of the release plan can be deferred")
    return deferred


def require_no_deferred_prerequisite(
    pending_sql: list[Path], deferred: frozenset[str], plan: ReleasePlan
) -> None:
    """Fail when candidate SQL depends on a transformation this environment defers."""
    artifacts = {phase.value for phase in plan.phases if phase.id in deferred}
    for path in pending_sql:
        for requirement in re.findall(
            r"^-- requires-data-migration: (\w+)$", path.read_text(), re.M
        ):
            if requirement in artifacts:
                raise ValueError("Pending SQL requires a deferred data migration")
