"""Own API and Agent worker as one local process group/configuration snapshot.

Run from backend with the same interpreter as the application. On restart both
children are stopped; changing .env cannot leave a worker on old credentials.
This owns only children it started, never processes discovered by name or port.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    configuration = {
        key: value
        for key, value in dotenv_values(args.env_file.resolve()).items()
        if value is not None
    }
    environment = {**os.environ, **configuration}
    if not configuration:
        parser.error("An explicit, non-empty local environment file is required")
    subprocess.run(
        [sys.executable, "-m", "src.platform.scope_sandbox.execution.preflight"],
        cwd=backend,
        env=environment,
        check=True,
        timeout=40,
    )
    children = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = [
        ["-m", "src.platform.access.adapters.agent.runtime.worker"],
        [
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--no-access-log",
            "--timeout-graceful-shutdown",
            "20",
        ],
    ]
    try:
        for command in commands:
            children.append(
                subprocess.Popen([sys.executable, *command], cwd=backend, env=environment)
            )
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.25)
        if not stopping:
            raise SystemExit("A local service exited; stopping its peer")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 30
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
