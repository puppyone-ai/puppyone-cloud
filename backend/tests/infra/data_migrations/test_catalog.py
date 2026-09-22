from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from src.infra.data_migrations.catalog import DataMigrationCatalog
from src.infra.data_migrations.errors import ManifestError


def _repository(tmp_path: Path, *, entrypoint: str = "run.sql") -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / "supabase").mkdir()
    (tmp_path / "supabase" / "config.toml").write_text("project_id='test'\n")
    migration = tmp_path / "supabase" / "data_migrations" / "20260712_example"
    migration.mkdir(parents=True)
    migration.joinpath("manifest.yml").write_text(
        "\n".join(
            (
                "api_version: 1",
                "id: 20260712_example",
                "description: Example immutable data migration",
                "kind: sql",
                f"entrypoint: {entrypoint}",
                "verify: verify.sql",
                "requires_schema:",
                '  - "20260712010000"',
                "required_env: []",
            )
        )
        + "\n"
    )
    migration.joinpath("run.sql").write_text("SELECT 1;\n")
    migration.joinpath("verify.sql").write_text("SELECT 1;\n")
    return tmp_path


def test_catalog_loads_and_checksums_complete_artifact(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    artifact = DataMigrationCatalog(repository).get("20260712_example")

    assert artifact.manifest.id == artifact.directory.name
    assert len(artifact.checksum) == 64
    assert artifact.entrypoint_path.name == "run.sql"


def test_repository_manifests_match_published_json_schema() -> None:
    repository = Path(__file__).resolve().parents[4]
    root = repository / "supabase/data_migrations"
    schema = json.loads((root / "manifest.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    for manifest_path in sorted(root.glob("*/manifest.yml")):
        validator.validate(yaml.safe_load(manifest_path.read_text()))


def test_checksum_changes_when_executable_content_changes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    catalog = DataMigrationCatalog(repository)
    original = catalog.get("20260712_example").checksum

    run_sql = repository / "supabase/data_migrations/20260712_example/run.sql"
    run_sql.write_text("SELECT 2;\n")

    assert catalog.get("20260712_example").checksum != original


def test_checksum_is_stable_across_line_endings(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    migration = repository / "supabase/data_migrations/20260712_example"
    for name in ("run.sql", "verify.sql"):
        migration.joinpath(name).write_bytes(b"SELECT 1;\n")

    catalog = DataMigrationCatalog(repository)
    original = catalog.get("20260712_example").checksum

    for name in ("run.sql", "verify.sql"):
        migration.joinpath(name).write_bytes(b"SELECT 1;\r\n")

    assert catalog.get("20260712_example").checksum == original


def test_manifest_cannot_escape_its_versioned_directory(tmp_path: Path) -> None:
    repository = _repository(tmp_path, entrypoint="../run.sql")

    with pytest.raises(ManifestError, match="inside the migration directory"):
        DataMigrationCatalog(repository).load_all()


def test_manifest_entrypoint_uses_portable_snake_case_filename(tmp_path: Path) -> None:
    repository = _repository(tmp_path, entrypoint="Run.sql")
    migration = repository / "supabase/data_migrations/20260712_example"
    migration.joinpath("Run.sql").write_text("SELECT 1;\n")

    with pytest.raises(ManifestError, match="snake_case"):
        DataMigrationCatalog(repository).load_all()


def test_manifest_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manifest = repository / "supabase/data_migrations/20260712_example/manifest.yml"
    manifest.write_text(manifest.read_text() + "kind: python\n")

    with pytest.raises(ManifestError, match="duplicate key"):
        DataMigrationCatalog(repository).load_all()


def test_every_migration_directory_requires_a_manifest(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "supabase/data_migrations/20260713_incomplete").mkdir()

    with pytest.raises(ManifestError, match="missing manifest"):
        DataMigrationCatalog(repository).load_all()


def test_data_migration_directory_cannot_be_a_symlink(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    outside = repository / "outside_migration"
    outside.mkdir()
    try:
        (repository / "supabase/data_migrations/20260713_link").symlink_to(
            outside,
            target_is_directory=True,
        )
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create the symlink attack fixture")
        raise

    with pytest.raises(ManifestError, match="cannot be symlinks"):
        DataMigrationCatalog(repository).load_all()


def test_sql_artifact_cannot_escape_runner_owned_transaction(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_sql = repository / "supabase/data_migrations/20260712_example/run.sql"
    run_sql.write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n")

    with pytest.raises(ManifestError, match="owns the SQL transaction"):
        DataMigrationCatalog(repository).load_all()


@pytest.mark.parametrize(
    "source, message",
    (
        ("\\copy public.example TO PROGRAM 'env'\n", "meta-commands"),
        ("COPY public.example TO PROGRAM 'env';\n", "COPY PROGRAM"),
        ("START TRANSACTION;\nSELECT 1;\n", "owns the SQL transaction"),
    ),
)
def test_sql_artifact_cannot_execute_outside_the_runner_boundary(
    tmp_path: Path,
    source: str,
    message: str,
) -> None:
    repository = _repository(tmp_path)
    run_sql = repository / "supabase/data_migrations/20260712_example/run.sql"
    run_sql.write_text(source)

    with pytest.raises(ManifestError, match=message):
        DataMigrationCatalog(repository).load_all()


@pytest.mark.parametrize(
    "source, message",
    (
        ("from src.config import settings\n", "mutable application package"),
        ("from .helper import migrate\n", "relative imports"),
    ),
)
def test_python_artifact_cannot_import_mutable_repository_code(
    tmp_path: Path,
    source: str,
    message: str,
) -> None:
    repository = _repository(tmp_path, entrypoint="run.py")
    migration = repository / "supabase/data_migrations/20260712_example"
    manifest = migration / "manifest.yml"
    manifest.write_text(manifest.read_text().replace("kind: sql", "kind: python"))
    migration.joinpath("run.py").write_text(source)

    with pytest.raises(ManifestError, match=message):
        DataMigrationCatalog(repository).load_all()


def test_python_artifact_must_be_one_self_contained_file(tmp_path: Path) -> None:
    repository = _repository(tmp_path, entrypoint="run.py")
    migration = repository / "supabase/data_migrations/20260712_example"
    manifest = migration / "manifest.yml"
    manifest.write_text(manifest.read_text().replace("kind: sql", "kind: python"))
    migration.joinpath("run.py").write_text("print('ok')\n")
    migration.joinpath("helper.py").write_text("VALUE = 1\n")

    with pytest.raises(ManifestError, match="single self-contained file"):
        DataMigrationCatalog(repository).load_all()
