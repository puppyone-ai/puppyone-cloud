"""Owned loopback services: Docker in CI, optional native binaries on macOS.

Only executables may be supplied, never existing service URLs or data paths.
Every process/container and temporary data directory belongs to this test run.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


@contextmanager
def owned_http_service(kind, *, database_url=None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    name = f"puppyone-test-{kind}-{uuid4()}"
    executable = os.environ.get(f"PUPPYONE_TEST_{kind.upper()}_BIN")
    process = None
    with tempfile.TemporaryDirectory(prefix=name) as directory:
        log = Path(directory) / "service.log"
        if kind == "postgrest":
            db_port = urlparse(database_url).port
            host = "127.0.0.1" if executable or sys.platform == "linux" else "host.docker.internal"
            env = {
                "PGRST_DB_URI": f"postgresql://postgres@{host}:{db_port}/postgres",
                "PGRST_DB_SCHEMAS": "public",
                "PGRST_DB_ANON_ROLE": "service_role",
                "PGRST_SERVER_HOST": "127.0.0.1" if executable else "0.0.0.0",
                "PGRST_SERVER_PORT": str(port),
            }
            image = ["postgrest/postgrest:v14.13"]
            arguments = []
            health = "/agent_runs?select=id&limit=1"
        elif kind == "minio":
            env = {
                "MINIO_ROOT_USER": "agent-fixture",
                "MINIO_ROOT_PASSWORD": "agent-fixture-password",
                "MINIO_BROWSER": "off",
            }
            arguments = [
                "server",
                directory if executable else "/data",
                "--address",
                f"127.0.0.1:{port}" if executable else f":{port}",
            ]
            image = ["--entrypoint", "/go/bin/minio", "puppyone-entrypoint-minio-build:local"]
            health = "/minio/health/ready"
        else:
            raise ValueError(f"Unsupported owned service: {kind}")
        url = f"http://127.0.0.1:{port}"
        try:
            if executable:
                with log.open("wb") as output:
                    process = subprocess.Popen(
                        [executable, *arguments],
                        env={**os.environ, **env},
                        stdout=output,
                        stderr=output,
                    )
            else:
                network = (
                    ["--network", "host"]
                    if sys.platform == "linux"
                    else ["-p", f"127.0.0.1:{port}:{port}"]
                )
                flags = [item for key, value in env.items() for item in ("-e", f"{key}={value}")]
                subprocess.run(
                    ["docker", "run", "-d", "--name", name, *network, *flags, *image, *arguments],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            with httpx.Client(trust_env=False, timeout=1) as probe:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        if probe.get(url + health).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process and process.poll() is not None:
                        raise RuntimeError(log.read_text()[-2000:])
                    time.sleep(0.1)
                else:
                    raise RuntimeError(
                        f"Owned {kind} did not become ready: {log.read_text()[-2000:] if executable else name}"
                    )
            yield url
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            elif not executable:
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, check=False, timeout=15
                )
