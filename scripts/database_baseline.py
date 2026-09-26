#!/usr/bin/env python3
"""Build/verify a Cloud baseline using only an owned, ephemeral Supabase stack.

No remote connection arguments and no hosted-history mutation are supported.
Historical SQL remains authoritative until a separate baseline adoption release.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase/migrations"
REFERENCE_DATA = ROOT / "supabase/baselines/required_data.sql"
NAME = re.compile(r"(?P<version>\d{14})_[a-z0-9_]+\.sql")
EXTENSION = re.compile(r'CREATE EXTENSION IF NOT EXISTS "?([a-z_0-9-]+)"?', re.I)
INVENTORY_TABLE = "project_storage_inventory_state"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(*args: str, input_text: str | None = None, capture: bool = True) -> str:
    result = subprocess.run(
        list(args),
        input=input_text,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=1200,
    )
    return result.stdout or ""


def source_files(cutoff: str | None = None) -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    versions: set[str] = set()
    for path in files:
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink():
            raise ValueError(f"Invalid migration path: {path.name}")
        version = match["version"]
        if version in versions:
            raise ValueError(f"Duplicate migration version: {version}")
        versions.add(version)
    if cutoff is not None and cutoff not in versions:
        raise ValueError("Candidate cutoff is absent from migration history")
    files = [path for path in files if cutoff is None or path.name[:14] <= cutoff]
    if not files:
        raise ValueError("No source migrations")
    return files


def normalize_dump(sql: str) -> str:
    # PostgreSQL 17 adds random psql restriction keys. They are transport
    # protection, not schema. Do not strip comments inside function definitions.
    return (
        "\n".join(
            line
            for line in sql.splitlines()
            if not line.startswith(
                (
                    "\\restrict ",
                    "\\unrestrict ",
                    "-- Dumped from database version",
                    "-- Dumped by pg_dump version",
                )
            )
        ).strip()
        + "\n"
    )


def managed_default_acls(sql: str) -> set[str]:
    """Supabase owns these role defaults; ordinary postgres cannot reapply them."""
    return set(
        re.findall(
            r'^ALTER DEFAULT PRIVILEGES FOR ROLE "supabase_[^"]+"[^\n]*;$', sql, re.MULTILINE
        )
    )


def validate_fresh_rows(counts: dict[str, int]) -> None:
    nonempty = {name: count for name, count in counts.items() if count}
    if nonempty != {INVENTORY_TABLE: 1}:
        raise ValueError(f"Unreviewed fresh-install reference tables: {nonempty}")


def validate_candidate(candidate: Path) -> tuple[dict, list[Path]]:
    manifest = json.loads((candidate / "manifest.json").read_text())
    if manifest.get("api_version") != 1 or manifest.get("status") != "candidate":
        raise ValueError("Unsupported baseline manifest")
    files = source_files(manifest["cutoff_version"])
    actual = {path.name: digest(path.read_bytes()) for path in files}
    if actual != manifest["source_migrations"]:
        raise ValueError("Baseline source migration inventory changed")
    for name in ("baseline.sql", "required_data.sql"):
        path = candidate / name
        if path.is_symlink() or digest(path.read_bytes()) != manifest["files"][name]:
            raise ValueError(f"Baseline artifact changed: {name}")
    return manifest, files


class LocalStack:
    def __init__(self, directory: Path, files: list[Path]):
        self.directory = directory
        self.project = "puppy-baseline-" + directory.name[-8:].lower().replace("_", "x")
        self.container = "supabase_db_" + self.project
        self.supabase = directory / "supabase"
        self.supabase.mkdir()
        (self.supabase / "config.toml").write_text(
            f'project_id = "{self.project}"\n'
            "[db]\nmajor_version = 17\nport = 55392\nshadow_port = 55390\n"
            "[db.seed]\nenabled = false\n"
            "[api]\nport = 55391\n"
            "[studio]\nenabled = false\n"
            "[inbucket]\nport = 55394\n"
            "[analytics]\nenabled = false\n"
        )
        self.replace_migrations(files)
        shutil.copytree(ROOT / "supabase/tests", self.supabase / "tests")

    def cli(self, *args: str, capture: bool = False) -> str:
        return run("supabase", *args, "--workdir", str(self.directory), capture=capture)

    def replace_migrations(self, files: list[Path]) -> None:
        target = self.supabase / "migrations"
        if target.exists():
            shutil.rmtree(target)  # Only the tool-owned TemporaryDirectory.
        target.mkdir()
        for path in files:
            shutil.copyfile(path, target / path.name)

    def sql(self, sql: str) -> str:
        return run(
            "docker",
            "exec",
            "-i",
            self.container,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            input_text=sql,
        ).strip()

    def dump(self) -> str:
        return normalize_dump(
            run(
                "docker",
                "exec",
                self.container,
                "pg_dump",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "--schema-only",
                "--schema=public",
                "--quote-all-identifiers",
            )
        )

    def auth_triggers(self) -> str:
        return (
            self.sql("""
SET search_path = pg_catalog;
SELECT pg_get_triggerdef(t.oid, false) || ';' ||
       CASE WHEN t.tgenabled='O' THEN '' ELSE chr(10) ||
       format('ALTER TABLE %I.%I %s TRIGGER %I;', n.nspname, c.relname,
         CASE t.tgenabled WHEN 'D' THEN 'DISABLE' WHEN 'R' THEN 'ENABLE REPLICA'
              WHEN 'A' THEN 'ENABLE ALWAYS' END, t.tgname) END
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_proc p ON p.oid=t.tgfoid
JOIN pg_namespace fn ON fn.oid=p.pronamespace
WHERE NOT t.tgisinternal AND n.nspname <> 'public' AND fn.nspname='public'
ORDER BY n.nspname, c.relname, t.tgname;
""")
            + "\n"
        )

    def reference_state(self) -> dict:
        if self.sql("SELECT count(*) FROM auth.users") != "0":
            raise ValueError("Refusing to capture a database containing users")
        self.sql((ROOT / "supabase/tests/_support/standalone_database.inc").read_text())
        names = self.sql("""
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p')
AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass
  AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname;
""").splitlines()
        counts = {
            name: int(self.sql('SELECT count(*) FROM public."' + name.replace('"', '""') + '"'))
            for name in names
        }
        validate_fresh_rows(counts)
        state = json.loads(
            self.sql("""
SELECT to_jsonb(s) - 'updated_at' FROM public.project_storage_inventory_state s;
""")
        )
        if state["inventory_complete"] is not False:
            raise ValueError("Fresh baseline cannot claim external inventory completion")
        return {"table_counts": counts, "inventory_state": state}

    def capture(self) -> dict:
        return {
            "schema": self.dump(),
            "auth_triggers": self.auth_triggers(),
            "reference_data": self.reference_state(),
        }


def render_baseline(
    stack: LocalStack, files: list[Path], state: dict, data: str, platform_acls: set[str]
) -> str:
    extensions = sorted(set().union(*(EXTENSION.findall(path.read_text()) for path in files)))
    # These names are parsed from immutable repository SQL, never a remote DSN.
    extension_sql = stack.sql(
        """
SELECT format('CREATE EXTENSION IF NOT EXISTS %I WITH SCHEMA %I;', e.extname, n.nspname)
FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
WHERE e.extname IN ("""
        + ",".join("'" + name + "'" for name in extensions)
        + ") ORDER BY e.extname;"
    )
    schema = state["schema"].replace(
        'CREATE SCHEMA "public";', 'CREATE SCHEMA IF NOT EXISTS "public";'
    )
    if managed_default_acls(schema) != platform_acls:
        raise ValueError("Historical migrations changed platform-owned default privileges")
    # They are already installed by Supabase. Preserve/compare them in the full
    # schema fingerprint, but do not ask the migration role to recreate them.
    schema = "\n".join(line for line in schema.splitlines() if line not in platform_acls) + "\n"
    return (
        "-- GENERATED BASELINE CANDIDATE: fresh Supabase databases only.\n"
        f"-- Covers {len(files)} migrations through {files[-1].name[:14]}.\n"
        "-- Do not add alongside the covered migration files.\n\n"
        + extension_sql
        + "\n\n"
        + schema
        + "\n"
        + "-- Application-owned triggers on platform tables.\n"
        + state["auth_triggers"]
        + "\n-- Reviewed fresh-install reference data.\n"
        + data
    )


def verify_equivalence(expected: dict, actual: dict) -> None:
    for component in ("schema", "auth_triggers", "reference_data"):
        if expected[component] != actual[component]:
            if isinstance(expected[component], str) and isinstance(actual[component], str):
                difference = difflib.unified_diff(
                    expected[component].splitlines(),
                    actual[component].splitlines(),
                    fromfile="historical-replay",
                    tofile="baseline-install",
                    lineterm="",
                )
                print("\n".join(list(difference)[:100]), flush=True)
            raise ValueError(f"Baseline differs from historical replay: {component}")


def execute(mode: str, destination: Path) -> None:
    manifest, files = validate_candidate(destination) if mode == "verify" else ({}, source_files())
    all_files = source_files()
    tail = [path for path in all_files if path.name[:14] > files[-1].name[:14]]
    if mode == "prepare" and destination.exists():
        raise ValueError("Output must be a new directory; candidates are never overwritten")
    with tempfile.TemporaryDirectory(prefix="puppy-baseline-") as temporary:
        stack = LocalStack(Path(temporary), [])
        try:
            print(f"Replaying {len(files)} migrations in an isolated Supabase stack", flush=True)
            stack.cli("start")
            platform_acls = managed_default_acls(stack.dump())
            stack.replace_migrations(files)
            stack.cli("migration", "up", "--local")
            expected = stack.capture()
            data = (
                (destination / "required_data.sql").read_text()
                if mode == "verify"
                else REFERENCE_DATA.read_text()
            )
            baseline = render_baseline(stack, files, expected, data, platform_acls)
            if mode == "verify" and baseline != (destination / "baseline.sql").read_text():
                raise ValueError("Candidate is not reproducible from its pinned migration history")
            latest = expected
            if tail:
                stack.replace_migrations(all_files)
                stack.cli("migration", "up", "--local")
                latest = stack.capture()
            generated = Path(temporary) / f"{files[-1].name[:14]}_baseline_b1.sql"
            generated.write_text(baseline)
            stack.replace_migrations([generated])
            print("Verifying fresh baseline against complete historical replay", flush=True)
            stack.cli("db", "reset", "--local", "--no-seed")
            actual = stack.capture()
            verify_equivalence(expected, actual)
            if tail:
                stack.replace_migrations([generated, *tail])
                stack.cli("migration", "up", "--local")
                verify_equivalence(latest, stack.capture())
            stack.cli("test", "db")
            # Real upgrade from a fixed existing release, with synthetic user
            # data. This is still the original upgrade chain, not adoption.
            stack.replace_migrations([p for p in all_files if p.name[:14] <= "20260720000000"])
            stack.cli("db", "reset", "--local", "--no-seed")
            stack.sql((ROOT / "supabase/test_fixtures/standalone_upgrade.sql").read_text())
            stack.replace_migrations(all_files)
            stack.cli("migration", "up", "--local")
            stack.sql((ROOT / "supabase/test_fixtures/standalone_upgrade_assert.sql").read_text())
            if stack.dump() != latest["schema"] or stack.auth_triggers() != latest["auth_triggers"]:
                raise ValueError("Existing-data upgrade did not reach the same schema")
            stack.cli("test", "db")
            if mode == "prepare":
                destination.mkdir(parents=True)
                (destination / "baseline.sql").write_text(baseline)
                (destination / "required_data.sql").write_text(data)
                manifest = {
                    "api_version": 1,
                    "id": "b1",
                    "status": "candidate",
                    "cutoff_version": files[-1].name[:14],
                    "source_commit": run("git", "-C", str(ROOT), "rev-parse", "HEAD").strip(),
                    "source_migrations": {p.name: digest(p.read_bytes()) for p in files},
                    "files": {
                        name: digest((destination / name).read_bytes())
                        for name in ("baseline.sql", "required_data.sql")
                    },
                    "required_platform": "Supabase PostgreSQL 17 with Auth and standard roles",
                    "reference_data": expected["reference_data"],
                    "activation": "not_performed",
                }
                (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            report = {
                "baseline_id": manifest["id"],
                "source_migration_count": len(files),
                "post_baseline_migrations_checked": [p.name for p in tail],
                "baseline_sha256": digest(baseline.encode()),
                "supabase_cli": run("supabase", "--version").strip(),
                "postgres": stack.sql("SHOW server_version"),
                "verified": [
                    "schema_owners_acl_rls",
                    "platform_triggers",
                    "reference_data",
                    "fresh_install_pgtap",
                    "populated_upgrade_pgtap",
                ],
                "adoption_verified": False,
                "hosted_databases_accessed": False,
            }
            report_path = (
                destination / "verification.json"
                if mode == "prepare"
                else Path(temporary) / "verification.json"
            )
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2), flush=True)
        finally:
            stack.cli("stop", "--no-backup")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("--output", type=Path, required=True)
    commands.add_parser("verify").add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    execute(args.command, (args.output if args.command == "prepare" else args.candidate).resolve())


if __name__ == "__main__":
    main()
