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


def runtime_inputs(backend: Path, env_file: Path) -> tuple:
    """Watch source/config identity, never outputs, caches, logs or secrets."""
    paths = [*sorted((backend / "src").rglob("*.py")), Path(__file__).resolve(), env_file]
    result = []
    for path in paths:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(path), None, None))
    return tuple(result)


def stop_children(children):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the local API/worker pair after source or environment changes",
    )
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    env_file = args.env_file.resolve()
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
    while not stopping:
        identity = runtime_inputs(backend, env_file)
        configuration = {
            key: value for key, value in dotenv_values(env_file).items() if value is not None
        }
        if not configuration:
            parser.error("An explicit, non-empty local environment file is required")
        environment = {**os.environ, **configuration}
        # A failed check stops startup; never serve the previous configuration
        # or weaken authorization just to make a health check green.
        subprocess.run(
            [sys.executable, "-m", "src.platform.workspace.execution.preflight"],
            cwd=backend,
            env=environment,
            check=True,
            timeout=40,
        )
        if stopping:
            break
        if runtime_inputs(backend, env_file) != identity:
            continue  # Source/config changed during preflight; check the new snapshot.
        children = []
        changed = False
        try:
            for command in commands:
                children.append(
                    subprocess.Popen([sys.executable, *command], cwd=backend, env=environment)
                )
            next_check = time.monotonic() + 1
            while not stopping and all(child.poll() is None for child in children):
                time.sleep(0.25)
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + 1
                    if runtime_inputs(backend, env_file) != identity:
                        if args.reload:
                            print(
                                "local_runtime_changed: gracefully restarting API and worker",
                                flush=True,
                            )
                            changed = True
                            break
                        print(
                            "local_runtime_stale: source/config changed; restart the pair or use --reload",
                            flush=True,
                        )
                        identity = runtime_inputs(backend, env_file)
            if not stopping and not changed:
                raise SystemExit("A local service exited; stopping its peer")
        finally:
            stop_children(children)
        if not changed:
            break


if __name__ == "__main__":
    main()
