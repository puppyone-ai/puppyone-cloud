"""Materialize protected environment configuration on an ephemeral Actions VM."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROLES = {
    "api", "frontend", "agent_worker", "upload_worker", "import_worker",
    "synchronize_worker", "mcp_server",
}


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
    for key in ("railway_token", "redis_url", "backup_bucket"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError("Release configuration is missing " + key)
    railway = config.get("railway", {})
    if not all(railway.get(key) for key in ("project_id", "environment_id")) or (
        set(railway.get("services", {})) != ROLES
    ):
        raise ValueError("Release configuration needs the complete Railway inventory")
    for key in ("frontend_build_args", "migration_environment", "acceptance"):
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
