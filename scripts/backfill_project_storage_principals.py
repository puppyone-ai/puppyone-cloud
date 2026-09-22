#!/usr/bin/env python3
"""Audit/backfill legacy Project storage principals before enabling deletion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from src.infra.s3.service import get_s3_service_instance  # noqa: E402
from src.platform.project.storage_inventory import (  # noqa: E402
    ProjectStorageInventoryRepository,
    run_project_storage_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Two-pass inventory of users/ objects and multipart uploads. "
            "Dry-run is the default; --apply records the ledger and opens deletion."
        )
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    s3 = get_s3_service_instance()
    proof = run_project_storage_inventory(
        s3.client,
        s3.bucket_name,
        ProjectStorageInventoryRepository(),
        apply=args.apply,
    )
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "object_count": proof.object_count,
                "multipart_count": proof.multipart_count,
                "digest": proof.digest,
                "deletion_enabled": args.apply,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
