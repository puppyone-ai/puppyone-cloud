"""Credential isolation and trusted-source admission on GitHub-hosted runners."""

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "supabase/releases/github_actions.py"
spec = importlib.util.spec_from_file_location("release_actions", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def source(tmp_path):
    config = {
        "format_version": 1,
        "environment": "staging",
        "railway_token": "fixture-token",
        "railway": {
            "project_id": "project",
            "environment_id": "environment",
            "services": {role: {"id": role} for role in module.ROLES},
        },
        "redis_url": "redis://localhost:6379/0",
        "backup_bucket": "private-backup",
        "migration_environment": {"SUPABASE_KEY": "stale-key"},
        "frontend_build_args": {},
        "acceptance": {
            "api_url": "https://api.example.test", "org_id": "org",
            "email": "release@example.test", "password": "fixture-password",
        },
        "database_url": "must-not-be-used",
        "private_artifacts": "/must-not-be-written",
    }
    return {
        "TARGET_ENVIRONMENT": "staging",
        "GITHUB_REPOSITORY": "puppyone-ai/puppyone-cloud",
        "GITHUB_REF": "refs/heads/qubits",
        "GITHUB_EVENT_NAME": "push",
        "RELEASE_CONFIG_JSON": json.dumps(config),
        "DATABASE_URL": "postgresql://protected-database",
        "SUPABASE_URL": "https://protected.example.test",
        "SUPABASE_PROJECT_ID": "protected-project",
        "SUPABASE_SERVICE_ROLE_KEY": "protected-key",
        "PGOPTIONS": "-c role=postgres",
        "RUNNER_TEMP": str(tmp_path),
    }


def test_configuration_uses_protected_database_and_private_ephemeral_files(source):
    destination = module.prepare(source)
    config = json.loads(destination.read_text())
    assert config["database_url"] == source["DATABASE_URL"]
    assert config["supabase_project_id"] == source["SUPABASE_PROJECT_ID"]
    assert config["migration_environment"]["SUPABASE_KEY"] == "protected-key"
    assert config["migration_environment"]["PGOPTIONS"] == "-c role=postgres"
    assert Path(config["private_artifacts"]).parent == destination.parent
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("key,value", [
    ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_EVENT_NAME", "pull_request_target"),
    ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_REPOSITORY", "fork/puppyone-cloud"),
    ("TARGET_ENVIRONMENT", "unknown"),
])
def test_untrusted_sources_cannot_materialize_credentials(source, key, value):
    source[key] = value
    with pytest.raises(ValueError, match="protected branch"):
        module.prepare(source)
    assert not list(Path(source["RUNNER_TEMP"]).iterdir())


def test_mislabeled_environment_configuration_is_rejected(source):
    config = json.loads(source["RELEASE_CONFIG_JSON"])
    config["environment"] = "production"
    source["RELEASE_CONFIG_JSON"] = json.dumps(config)
    with pytest.raises(ValueError, match="different environment"):
        module.prepare(source)


def test_existing_private_directory_is_not_overwritten(source):
    destination = module.prepare(source)
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        module.prepare(source)
    assert destination.read_bytes() == before


def test_configuration_errors_never_echo_secret_values(source):
    source["RELEASE_CONFIG_JSON"] = '{"fixture-private-marker": invalid json}'
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], env={**os.environ, **source},
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "fixture-private-marker" not in result.stdout + result.stderr
    assert "JSONDecodeError" in result.stderr


def test_workflow_keeps_credentials_after_ci_and_out_of_public_artifacts():
    workflow = yaml.safe_load((ROOT / ".github/workflows/_environment-release.yml").read_text())
    jobs = workflow["jobs"]
    release = jobs["release"]
    # Application deployment waits for the database stage, the pinned sandbox
    # image and the application images built once for this commit.
    assert release["needs"] == ["database", "sandbox_image", "images"]
    assert release["runs-on"] == "ubuntu-24.04"
    assert release["environment"] == "${{ inputs.environment }}"
    steps = release["steps"]
    assert jobs["database"]["needs"] == "admission"
    assert any(s.get("name") == "Wait for candidate CI before changing the environment"
               for s in jobs["admission"]["steps"])
    assert "environment" not in jobs["admission"]
    artifacts = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(artifacts) == 1
    assert artifacts[0]["with"]["path"] == "${{ runner.temp }}/environment-release.json"
    assert steps[-1]["if"] == "always()"
    admission = jobs["admission"]["steps"][0]["run"]
    assert "GITHUB_REPOSITORY" in admission
    assert "GITHUB_EVENT_NAME" in admission
    assert "refs/heads/qubits:push" in admission
    assert "refs/heads/main:push" in admission


def test_sandbox_image_job_uses_only_image_credentials_after_ci():
    jobs = yaml.safe_load((ROOT / ".github/workflows/_environment-release.yml").read_text())["jobs"]
    image = jobs["sandbox_image"]
    assert image["needs"] == "admission"  # Admission waits for candidate CI.
    assert image["environment"] == "${{ inputs.environment }}"
    assert image["runs-on"] == "ubuntu-24.04"
    steps = image["steps"]
    assert all("continue-on-error" not in step for step in steps)
    assert "PUPPYONE_RELEASE_CONFIG" not in str(image)
    assert not any(step.get("uses", "").startswith("actions/upload-artifact@") for step in steps)
    # Every step after detection is disabled for environments without a Fly sandbox.
    assert steps[0]["id"] == "config"
    assert all(step.get("if") == "steps.config.outputs.enabled == 'true'" for step in steps[1:])
    publish = steps[-1]
    assert set(publish["env"]) == {
        "FLY_API_TOKEN", "FLY_APP", "RAILWAY_TOKEN", "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SANDBOX_IMAGE_SERVICES",
    }
    assert "--password-stdin" in publish["run"] and "docker logout" in publish["run"]
    assert "sandbox_image_release.py" in publish["run"]


BACKEND = "ghcr.io/puppyone-ai/puppyone-cloud-backend@sha256:" + "1" * 64
FRONTEND = "ghcr.io/puppyone-ai/puppyone-cloud-frontend@sha256:" + "2" * 64


def test_image_release_carries_digests_built_from_this_commit(source):
    source.update(
        GITHUB_SHA="c" * 40, RELEASE_IMAGES_SOURCE="c" * 40,
        RELEASE_BACKEND_IMAGE=BACKEND, RELEASE_FRONTEND_IMAGE=FRONTEND,
    )
    config = json.loads(source["RELEASE_CONFIG_JSON"])
    del config["frontend_build_args"]  # Only commit builds need build arguments.
    source["RELEASE_CONFIG_JSON"] = json.dumps(config)
    prepared = json.loads(module.prepare(source).read_text())
    assert prepared["images"] == {"backend": BACKEND, "frontend": FRONTEND}


@pytest.mark.parametrize("change", [
    {"RELEASE_IMAGES_SOURCE": "d" * 40},
    {"RELEASE_FRONTEND_IMAGE": ""},
    {"RELEASE_BACKEND_IMAGE": "ghcr.io/puppyone-ai/puppyone-cloud-backend:latest"},
])
def test_images_from_another_commit_or_mutable_tags_are_rejected(source, change):
    source.update(
        GITHUB_SHA="c" * 40, RELEASE_IMAGES_SOURCE="c" * 40,
        RELEASE_BACKEND_IMAGE=BACKEND, RELEASE_FRONTEND_IMAGE=FRONTEND,
    )
    source.update(change)
    with pytest.raises(ValueError):
        module.prepare(source)


def test_ordinary_release_does_not_require_a_backup_bucket(source):
    config = json.loads(source["RELEASE_CONFIG_JSON"])
    del config["backup_bucket"]
    source["RELEASE_CONFIG_JSON"] = json.dumps(config)
    assert "backup_bucket" not in json.loads(module.prepare(source).read_text())


def test_images_job_builds_once_and_publishes_with_the_workflow_token_only():
    jobs = yaml.safe_load((ROOT / ".github/workflows/_environment-release.yml").read_text())["jobs"]
    images = jobs["images"]
    assert images["needs"] == "admission"  # Admission waits for candidate CI.
    assert images["environment"] == "${{ inputs.environment }}"
    assert images["permissions"] == {"contents": "read", "packages": "write"}
    assert "PUPPYONE_RELEASE_CONFIG" not in str(images)
    steps = images["steps"]
    assert steps[0]["id"] == "config"
    assert all(step.get("if") == "steps.config.outputs.enabled == 'true'" for step in steps[1:])
    assert all("continue-on-error" not in step for step in steps)
    publish = steps[-1]
    assert publish["env"] == {"REGISTRY_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert "--password-stdin" in publish["run"] and "docker logout" in publish["run"]
    names = [step.get("name", "") for step in steps]
    # Smoke tests run before anything is published.
    assert names.index("Smoke-test the backend image") < names.index("Publish the images")
    assert names.index("Smoke-test the frontend image") < names.index("Publish the images")
    configuration = next(s for s in jobs["release"]["steps"] if s.get("id") == "configuration")
    assert configuration["env"]["RELEASE_BACKEND_IMAGE"] == "${{ needs.images.outputs.backend }}"


def test_workflows_fetch_the_postgresql_signing_key_from_its_published_location():
    # apt.postgresql.org/media/keys/ returns 404; the key is published on www.
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        text = workflow.read_text()
        assert "apt.postgresql.org/media/keys" not in text, workflow.name
    release = (ROOT / ".github/workflows/_environment-release.yml").read_text()
    assert "https://www.postgresql.org/media/keys/ACCC4CF8.asc" in release
