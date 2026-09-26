#!/usr/bin/env python3
"""Stage archived upgrades or verify/adopt B1 using an explicit environment DSN."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from src.infra.data_migrations.baseline_adoption import adoption_sql  # noqa: E402
from src.infra.data_migrations.database import PsqlClient  # noqa: E402
from src.infra.data_migrations.errors import DataMigrationError  # noqa: E402
from src.infra.data_migrations.schema_history import load_baseline  # noqa: E402


def stage_history(destination: Path) -> None:
    baseline = load_baseline(ROOT)
    if not baseline:
        raise ValueError("No active baseline")
    # Never overwrite a checkout, linked project, or an operator's existing work.
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / "supabase"
    (target / "migrations").mkdir(parents=True)
    shutil.copyfile(ROOT / "supabase/config.toml", target / "config.toml")
    for name in baseline["source_migrations"]:
        shutil.copyfile(ROOT / baseline["archive"] / name, target / "migrations" / name)
    shutil.copytree(ROOT / "supabase/tests", target / "tests")
    print(f"Archived upgrade workdir: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="materialize the immutable pre-B1 migration chain")
    stage.add_argument("--output", type=Path, required=True)
    commands.add_parser("check", help="verify admission without committing any changes")
    commands.add_parser("adopt", help="verify and atomically replace covered history only")
    args = parser.parse_args()
    if args.command == "stage":
        stage_history(args.output.resolve())
        return
    db = PsqlClient(
        os.environ.get("DATA_MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    )
    project = os.environ.get("SUPABASE_PROJECT_ID")
    if project:
        db.assert_supabase_target(project_ref=project, api_url=os.environ.get("SUPABASE_URL", ""))
    db.command(
        ["-q", "-v", "ON_ERROR_STOP=1"],
        timeout=180,
        input_text=adoption_sql(ROOT, apply=args.command == "adopt"),
    )
    print(
        "Baseline admission verified; "
        + ("history aligned." if args.command == "adopt" else "no changes committed.")
    )


if __name__ == "__main__":
    try:
        main()
    except (DataMigrationError, ValueError, OSError) as error:
        # Database errors can contain data. Only expose our fixed diagnostic code.
        import re

        code = re.search(r"BASELINE_[A-Z_]+", str(error))
        raise SystemExit(
            code.group(0) if code else f"Baseline admission failed ({type(error).__name__})"
        ) from None
