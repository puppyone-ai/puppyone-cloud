"""Public, checksum-pinned schema history. Archives are never deployment inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import ManifestError

B1_MANIFEST = Path("supabase/baselines/b1/manifest.json")
SQL_NAME = re.compile(r"[0-9]{14}_[a-z0-9_]+\.sql")


def load_baseline(root: Path) -> dict | None:
    path = root / B1_MANIFEST
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
        if manifest.get("status") == "candidate":
            return None
        if (
            manifest["api_version"] != 1
            or manifest["id"] != "b1"
            or manifest["status"] != "active"
            or manifest["archive"] != "supabase/archive/before_b1"
            or manifest["migration"] != "supabase/migrations/20260926000000_baseline_b1.sql"
        ):
            raise ValueError("unsupported baseline")
        archive = root / manifest["archive"]
        sources = manifest["source_migrations"]
        if not sources or set(sources) != {p.name for p in archive.glob("*.sql")}:
            raise ValueError("archive inventory differs")
        for name, checksum in sources.items():
            if not SQL_NAME.fullmatch(name):
                raise ValueError("invalid archived migration filename")
            verify_file(archive / name, checksum)
            if (root / "supabase/migrations" / name).exists():
                raise ValueError("covered source remains in active migrations")
        verify_file(root / manifest["migration"], manifest["files"]["baseline.sql"])
        return manifest
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ManifestError(f"invalid active baseline: {error}") from error


def verify_file(path: Path, checksum: str) -> None:
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise ValueError(f"immutable SQL checksum changed: {path.name}")


def historical_source(root: Path, name: str) -> Path:
    if not SQL_NAME.fullmatch(name):
        raise ManifestError("invalid schema filename")
    active = root / "supabase/migrations" / name
    if active.is_file():
        return active
    baseline = load_baseline(root)
    if baseline and name in baseline["source_migrations"]:
        return root / baseline["archive"] / name
    raise ManifestError(f"schema source is missing: {name}")


def covered_versions(baseline: dict) -> set[str]:
    return {name[:14] for name in baseline["source_migrations"]}


def baseline_version(baseline: dict) -> str:
    return Path(baseline["migration"]).name[:14]


def data_job_coverage(root: Path, applied: set[str], migration_id: str) -> tuple[set[str], bool]:
    baseline = load_baseline(root)
    if not baseline or baseline_version(baseline) not in applied:
        return applied, False
    disposition = baseline["data_jobs"].get(migration_id)
    return applied | covered_versions(baseline), disposition == "retired"
