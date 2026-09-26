#!/usr/bin/env python3
"""Run the documented installer on isolated volumes with synthetic credentials.

Only volumes belonging to the generated puppyone-install-* project are removed.
No hosted credentials, user's .env, Pay service or pre-existing session is read.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def jwt(role, secret):
    def encode(value):
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    message = f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode({'role': role, 'iss': 'supabase-demo', 'exp': int(time.time()) + 86400})}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    return f"{message}.{signature}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["default", "custom"], default="default")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    project = "puppyone-install-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        directory = Path(temporary)
        values = dict(
            line.split("=", 1)
            for line in (ROOT / "docker/.env.example").read_text().splitlines()
            if line and not line.startswith("#")
        )
        # Both variants use isolated ports; custom also proves rotated keys,
        # PostgreSQL credentials, and a non-default bucket/access key work.
        values.update(
            SUPABASE_API_PORT="18000",
            BACKEND_PORT="19090",
            FRONTEND_PORT="13000",
            DATABASE_PORT="15432",
            MINIO_CONSOLE_PORT="19001",
            MAIL_PORT="18025",
        )
        if args.variant == "custom":
            secret = secrets.token_hex(32)
            values.update(
                JWT_SECRET=secret,
                ANON_KEY=jwt("anon", secret),
                SERVICE_ROLE_KEY=jwt("service_role", secret),
                POSTGRES_PASSWORD=secrets.token_hex(16),
                S3_ACCESS_KEY="install-access",
                S3_SECRET_KEY=secrets.token_hex(16),
                S3_BUCKET="install-custom",
            )
        env_file = directory / "compose.env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        env_file.chmod(0o600)
        compose = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "docker/docker-compose.yml"),
            "-f",
            str(ROOT / "docker/compose.install-test.yml"),
        ]

        def run(*command, **kwargs):
            subprocess.run(command, check=True, cwd=ROOT, timeout=1800, **kwargs)

        try:
            run(*compose, "build")
            run(*compose, "up", "--detach", "--wait", "--wait-timeout", "420")
            run(*compose, "run", "--rm", "migrate")
            test_env = {
                **os.environ,
                "INSTALL_API": "http://127.0.0.1:19090",
                "INSTALL_AUTH": "http://127.0.0.1:18000",
                "INSTALL_WEB": "http://127.0.0.1:13000",
                "INSTALL_MAIL": "http://127.0.0.1:18025",
                "INSTALL_ANON_KEY": values["ANON_KEY"],
                "INSTALL_STATE": str(directory / "state.json"),
            }
            for phase in ("seed", "verify"):
                if phase == "verify":
                    # Preserve volumes, recreate every service, and rerun the
                    # same installer. Auth, DB rows and object bytes must survive.
                    run(*compose, "down")
                    # A broken pending migration must roll back and block the
                    # new application, while preserving existing user data.
                    broken = directory / "29991231000000_install_failure_probe.sql"
                    broken.write_text(
                        "CREATE TABLE public.install_failure_probe(id integer);\nSELECT 1/0;\n"
                    )
                    override = directory / "broken.json"
                    override.write_text(
                        json.dumps(
                            {
                                "services": {
                                    "migrate": {
                                        "volumes": [
                                            f"{broken}:/app/supabase/migrations/{broken.name}:ro"
                                        ]
                                    }
                                }
                            }
                        )
                    )
                    rejected = subprocess.run(
                        [
                            *compose,
                            "-f",
                            str(override),
                            "up",
                            "--detach",
                            "--wait",
                            "--wait-timeout",
                            "120",
                            "api",
                            "web",
                        ],
                        check=False,
                        cwd=ROOT,
                        timeout=240,
                    )
                    if rejected.returncode == 0:
                        raise RuntimeError(
                            "Broken migration incorrectly allowed startup"
                        )
                    running = subprocess.check_output(
                        [*compose, "ps", "--status", "running", "--services"],
                        text=True,
                        timeout=30,
                    ).splitlines()
                    if {"api", "web"} & set(running):
                        raise RuntimeError(
                            "Application started after migration failure"
                        )
                    proof = subprocess.check_output(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "db",
                            "psql",
                            "-U",
                            "postgres",
                            "-d",
                            "postgres",
                            "-XAt",
                            "-c",
                            "SELECT to_regclass('public.install_failure_probe') IS NULL AND NOT EXISTS (SELECT FROM supabase_migrations.schema_migrations WHERE version='29991231000000');",
                        ],
                        text=True,
                        timeout=30,
                    ).strip()
                    if proof != "t":
                        raise RuntimeError(
                            "Failed migration left DDL or a success receipt"
                        )
                    run(*compose, "down")
                    run(*compose, "up", "--detach", "--wait", "--wait-timeout", "420")
                run(
                    "npx",
                    "--prefix",
                    "e2e",
                    "playwright",
                    "test",
                    "--config=e2e/install/playwright.config.mjs",
                    env={
                        **test_env,
                        "INSTALL_PHASE": phase,
                        "INSTALL_REPORT": str(
                            args.artifacts.resolve() / f"{phase}.json"
                        ),
                    },
                )
            (args.artifacts / "result.json").write_text(
                json.dumps(
                    {
                        "variant": args.variant,
                        "result": "passed",
                        "schema_source": "supabase/migrations",
                        "restart_verified": True,
                    }
                )
                + "\n"
            )
        finally:
            with (args.artifacts / "compose.log").open("w") as log:
                subprocess.run(
                    [*compose, "logs", "--no-color", "--tail", "150"],
                    stdout=log,
                    stderr=log,
                    check=False,
                    timeout=60,
                )
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                check=True,
                timeout=120,
            )


if __name__ == "__main__":
    main()
