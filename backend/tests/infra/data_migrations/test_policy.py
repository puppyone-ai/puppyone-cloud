from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.infra.data_migrations.catalog import DataMigrationCatalog
from src.infra.data_migrations.errors import ManifestError
from src.infra.data_migrations.policy import ChangedPath, validate_repository_policy

from .test_catalog import _repository


def test_policy_rejects_modifying_existing_schema_migration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/20260712010000_expand_example.sql"
    migration.parent.mkdir()
    migration.write_text("CREATE TABLE example(id bigint);\n")

    with pytest.raises(ManifestError, match="immutable"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("M", migration.relative_to(repository).as_posix())],
        )


def test_policy_requires_timestamped_snake_case_schema_filename(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/Bad Migration.sql"
    migration.parent.mkdir()
    migration.write_text("SELECT 1;\n")

    with pytest.raises(ManifestError, match="timestamped snake_case"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_requires_schema_migrations_at_the_canonical_root(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/nested/20260712010000_example.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("SELECT 1;\n")

    with pytest.raises(ManifestError, match="direct files"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_rejects_duplicate_schema_versions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migrations = repository / "supabase/migrations"
    migrations.mkdir()
    first = migrations / "20260712010000_first.sql"
    second = migrations / "20260712010000_second.sql"
    first.write_text("SELECT 1;\n")
    second.write_text("SELECT 2;\n")

    with pytest.raises(ManifestError, match="duplicate schema migration version"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("A", first.relative_to(repository).as_posix())],
        )


def test_policy_grandfathers_only_exact_pre_governance_schema_bytes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    migrations = repository / "supabase/migrations"
    migrations.mkdir()
    legacy = migrations / "20260711070000_legacy_drop.sql"
    legacy.write_text("-- now run scripts/legacy.py\nDROP TABLE public.legacy;\n")
    baseline = repository / "supabase/data_migrations/schema_history_baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "api_version": 1,
                "schema_sha256": {legacy.name: hashlib.sha256(legacy.read_bytes()).hexdigest()},
            }
        )
    )
    catalog = DataMigrationCatalog(repository)

    validate_repository_policy(
        catalog,
        [ChangedPath("A", legacy.relative_to(repository).as_posix())],
    )

    legacy.write_text(legacy.read_text() + "SELECT 1;\n")
    with pytest.raises(ManifestError, match="checksum changed"):
        validate_repository_policy(catalog, [])


def test_schema_history_baseline_is_immutable_after_adoption(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    baseline = repository / "supabase/data_migrations/schema_history_baseline.json"
    baseline.write_text(json.dumps({"api_version": 1, "schema_sha256": {}}))

    with pytest.raises(ManifestError, match="baseline is immutable"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("M", baseline.relative_to(repository).as_posix())],
        )


def test_policy_rejects_external_code_contract_in_schema_sql(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/20260712010000_expand_example.sql"
    migration.parent.mkdir()
    migration.write_text("-- now run scripts/backfill.py\nSELECT 1;\n")

    with pytest.raises(ManifestError, match="external application step"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_requires_data_marker_on_destructive_contract(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/20260712020000_remove_example.sql"
    migration.parent.mkdir()
    migration.write_text("DROP TABLE public.example;\n")

    with pytest.raises(ManifestError, match="marked contract"):
        validate_repository_policy(
            DataMigrationCatalog(repository),
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_requires_contract_to_pin_current_artifact_checksum(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    catalog = DataMigrationCatalog(repository)
    migration = repository / "supabase/migrations/20260712020000_contract_example.sql"
    migration.parent.mkdir()
    migration.write_text(
        "-- requires-data-migration: 20260712_example\n"
        f"-- data-migration-checksum: {'0' * 64}\n"
        "DROP TABLE public.example;\n"
    )

    with pytest.raises(ManifestError, match="checksum does not match"):
        validate_repository_policy(
            catalog,
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_accepts_checksum_pinned_contract(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    catalog = DataMigrationCatalog(repository)
    checksum = catalog.get("20260712_example").checksum
    migration = repository / "supabase/migrations/20260712020000_contract_example.sql"
    migration.parent.mkdir()
    migration.write_text(
        "-- requires-data-migration: 20260712_example\n"
        f"-- data-migration-checksum: {checksum}\n"
        "DROP TABLE public.example;\n"
    )

    validate_repository_policy(
        catalog,
        [ChangedPath("A", migration.relative_to(repository).as_posix())],
    )


def test_policy_rejects_contract_that_differs_from_reviewed_pending_sql(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    catalog = DataMigrationCatalog(repository)
    artifact = catalog.get("20260712_example")
    reviewed = artifact.directory / "contract.pending.sql"
    reviewed.write_text(
        "-- requires-data-migration: 20260712_example\n"
        f"-- data-migration-checksum: {artifact.checksum}\n"
        "DROP TABLE public.example;\n"
    )
    migration = repository / "supabase/migrations/20260712020000_contract_example.sql"
    migration.parent.mkdir()
    migration.write_text(reviewed.read_text() + "DROP TABLE public.unreviewed;\n")

    with pytest.raises(ManifestError, match="reviewed pending contract"):
        validate_repository_policy(
            catalog,
            [ChangedPath("A", migration.relative_to(repository).as_posix())],
        )


def test_policy_accepts_exact_reviewed_pending_contract(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    catalog = DataMigrationCatalog(repository)
    artifact = catalog.get("20260712_example")
    reviewed = artifact.directory / "contract.pending.sql"
    reviewed.write_text(
        "-- requires-data-migration: 20260712_example\n"
        f"-- data-migration-checksum: {artifact.checksum}\n"
        "DROP TABLE public.example;\n"
    )
    migration = repository / "supabase/migrations/20260712020000_contract_example.sql"
    migration.parent.mkdir()
    migration.write_bytes(reviewed.read_bytes())

    validate_repository_policy(
        catalog,
        [ChangedPath("A", migration.relative_to(repository).as_posix())],
    )


def test_historical_compatibility_is_exact_and_cannot_admit_other_files(tmp_path: Path):
    repository = _repository(tmp_path)
    migration = repository / "supabase/migrations/20260716000000_remove_workspace_binding.sql"
    migration.parent.mkdir()
    migration.write_text("DROP TABLE public.example;\n")
    plan = repository / "supabase/releases/20260923_production_catchup.json"
    plan.parent.mkdir()
    payload = {
        "api_version": 1,
        "id": "20260923_production_catchup",
        "schema_sha256": {migration.name: hashlib.sha256(migration.read_bytes()).hexdigest()},
        "policy_compatibility": [migration.name],
    }
    plan.write_text(json.dumps(payload))
    catalog = DataMigrationCatalog(repository)
    validate_repository_policy(
        catalog, [ChangedPath("A", migration.relative_to(repository).as_posix())]
    )
    migration.write_text("DROP TABLE public.other;\n")
    with pytest.raises(ManifestError, match="checksum changed"):
        validate_repository_policy(catalog, [])
    payload["policy_compatibility"].append("20260923000000_unreviewed.sql")
    plan.write_text(json.dumps(payload))
    with pytest.raises(ManifestError, match="unreviewed compatibility"):
        validate_repository_policy(catalog, [])
