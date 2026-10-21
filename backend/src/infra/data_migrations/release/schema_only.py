"""Automatic compatible SQL deployment, independent of application infrastructure."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from ..database import PsqlClient
from .plan import ReleasePlan
from .target import DatabaseTarget


def deploy_schema(plan: ReleasePlan, target: DatabaseTarget) -> dict:
    """Classify the entire pending upgrade under the shared database lock.

    Dependent Contract upgrades hand off without applying candidate SQL. The
    full coordinator owns data artifacts, drain, backup and dependent SQL.
    Ordinary SQL never constructs a Railway, Redis or object-store client.
    """
    with target.lock() as guard:
        guard.assert_held()
        target.schema_executor.adopt()
        # Unrelated historical data artifacts are not SQL prerequisites. They
        # remain pending for the application coordinator; this stage neither
        # executes them nor fabricates their completion receipts.
        pending = [
            phase for phase in plan.phases
            if phase.kind == "schema" and target.pending(phase)
        ]
        files = target.schema_executor.pending("latest")
        result = {"pending_versions": [path.name[:14] for path in files]}
        if target.requires_quiescence(pending) or any(
            "-- requires-data-migration:" in path.read_text() for path in files
        ):
            return {
                **result,
                "state": "coordinated_release_required",
                "pending_data": [
                    phase.value for phase in plan.phases
                    if phase.kind == "data" and target.pending(phase)
                ],
            }
        guard.assert_held()
        target.db.command(
            ["-q", "-v", "ON_ERROR_STOP=1", "-f",
             str(target.root / "supabase/releases/legacy_credential_preflight.sql")],
        )
        for phase in pending:
            guard.assert_held()
            target.schema(phase)
        guard.assert_held()
        target.verify_database()
        guard.assert_held()
        return {**result, "state": "deployed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = {"state": "failed", "environment": args.environment, "source_sha": args.source}
    try:
        branch = "qubits" if args.environment == "staging" else "main"
        if (
            os.environ.get("GITHUB_REPOSITORY") != "puppyone-ai/puppyone-cloud"
            or os.environ.get("GITHUB_REF") != f"refs/heads/{branch}"
            or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
            or os.environ.get("GITHUB_SHA") != args.source
            or not re.fullmatch(r"[a-f0-9]{40}", args.source)
        ):
            raise ValueError("Schema deployment requires the protected source")
        root = Path(__file__).resolve().parents[5]
        database = PsqlClient(os.environ["DATABASE_URL"])
        database.assert_supabase_target(
            project_ref=os.environ["SUPABASE_PROJECT_ID"], api_url=os.environ["SUPABASE_URL"]
        )
        target = DatabaseTarget(root, database, platform=None, environment=os.environ)
        plan = ReleasePlan.load(root)
        result.update(deploy_schema(plan, target), plan_checksum=plan.checksum)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"schema_state={result['state']}\n")
        return 0
    except Exception as error:
        # A psql diagnostic may contain customer data. Only the error class is
        # public; the result never asserts that failed/skipped SQL was applied.
        result["error_type"] = type(error).__name__
        print(f"::error::Database migration failed ({type(error).__name__})", flush=True)
        return 1
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
