"""Materialize protected environment configuration on an ephemeral Actions VM."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROLES = {
    "api", "frontend", "agent_worker", "upload_worker", "import_worker",
    "synchronize_worker", "mcp_server",
}
OPTIONAL_ROLES = {"ssh_gateway"}


def release_images(source: dict[str, str]) -> dict[str, str] | None:
    """Digest-pinned images the `images` job published for this exact commit."""
    backend = source.get("RELEASE_BACKEND_IMAGE", "")
    frontend = source.get("RELEASE_FRONTEND_IMAGE", "")
    if not backend and not frontend:
        return None
    commit = source.get("GITHUB_SHA")
    if not commit or source.get("RELEASE_IMAGES_SOURCE") != commit:
        raise ValueError("Release images were not built from this source")
    for value, name in ((backend, "backend"), (frontend, "frontend")):
        if not re.fullmatch(
            rf"ghcr\.io/puppyone-ai/puppyone-cloud-{name}@sha256:[0-9a-f]{{64}}", value
        ):
            raise ValueError("Release images must be digest-pinned published images")
    return {"backend": backend, "frontend": frontend}


def prepare(source: dict[str, str]) -> Path:
    environment = source.get("TARGET_ENVIRONMENT")
    branch = {"staging": "qubits", "production": "main"}.get(environment)
    if (
        branch is None
        or source.get("GITHUB_REPOSITORY") != "puppyone-ai/puppyone-cloud"
        or source.get("GITHUB_REF") != "refs/heads/" + branch
        or source.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}
    ):
        raise ValueError("Release configuration requires its protected branch")
    raw = source.get("RELEASE_CONFIG_JSON", "")
    if not raw:
        raise ValueError("PUPPYONE_RELEASE_CONFIG is missing from the GitHub environment")
    config = json.loads(raw)
    if not isinstance(config, dict) or (
        config.get("format_version") != 1 or config.get("environment") != environment
    ):
        raise ValueError("Release configuration belongs to a different environment")
    for key in ("railway_token", "redis_url"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError("Release configuration is missing " + key)
    # Only breaking (quiescing) releases take a recovery point; the coordinator
    # proves the versioned backup bucket before it stops anything.
    if "backup_bucket" in config and not isinstance(config["backup_bucket"], str):
        raise TypeError("Release configuration backup_bucket must be a bucket name")
    # Bucket versioning is the default object recovery. "snapshot" is the
    # reviewed fallback for providers that cannot version buckets.
    recovery = config.get("object_recovery")
    if recovery is not None and (
        not isinstance(recovery, dict)
        or recovery.get("mode") not in {"versioning", "snapshot"}
        or (
            recovery["mode"] == "snapshot"
            and not all(
                isinstance(recovery.get(key), str) and recovery[key].strip()
                for key in ("approved_by", "reason")
            )
        )
    ):
        raise ValueError("Release configuration object_recovery is invalid")
    railway = config.get("railway", {})
    roles = set(railway.get("services", {}))
    if not all(railway.get(key) for key in ("project_id", "environment_id")) or not (
        ROLES <= roles <= ROLES | OPTIONAL_ROLES
    ):
        raise ValueError("Release configuration needs the complete Railway inventory")
    images = release_images(source)
    if images:
        config["images"] = images
    required = ("migration_environment", "acceptance")
    for key in required + (() if images else ("frontend_build_args",)):
        if not isinstance(config.get(key), dict):
            raise TypeError("Release configuration is missing " + key)
    for key in ("api_url", "org_id", "email", "password"):
        if not config["acceptance"].get(key):
            raise ValueError("Release acceptance configuration is incomplete")
    for key in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_PROJECT_ID", "SUPABASE_SERVICE_ROLE_KEY"):
        if not source.get(key):
            raise ValueError("Protected database configuration is missing " + key)
    # connection.py binds these values to the protected Supabase project before
    # this step. Do not let a JSON secret override that database authority.
    config.update(
        database_url=source["DATABASE_URL"],
        supabase_url=source["SUPABASE_URL"],
        supabase_project_id=source["SUPABASE_PROJECT_ID"],
    )
    config["migration_environment"].update(
        SUPABASE_URL=source["SUPABASE_URL"],
        SUPABASE_KEY=source["SUPABASE_SERVICE_ROLE_KEY"],
    )
    if source.get("PGOPTIONS"):
        config["migration_environment"]["PGOPTIONS"] = source["PGOPTIONS"]
    directory = Path(source["RUNNER_TEMP"]) / "puppyone-release-private"
    directory.mkdir(mode=0o700)
    config["private_artifacts"] = str(directory / "diagnostics")
    destination = directory / "configuration.json"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(config, output)
    return destination


if __name__ == "__main__":
    try:
        prepare(dict(os.environ))
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as error:
        # JSON parser errors and unexpected exception messages may contain
        # private configuration. Never print them to the workflow log.
        raise SystemExit(
            f"Release configuration invalid ({type(error).__name__}); check environment secrets"
        ) from None
    print("Protected release configuration prepared")
