"""Supervisor-owned private networking; independent of API source reloads."""

import json
import os
import subprocess
import time
from pathlib import Path

from src.platform.sandbox.providers.fly.agent import (
    MANAGED_AGENT,
    create_directory,
    identity,
    isolated_environment,
)


class LocalFlyAgent:
    def __init__(self):
        self.directory = self.process = self.configuration = None

    def healthy(self):
        return self.process is None or self.process.poll() is None

    def configure(self, environment):
        # A supervisor never inherits another launcher's managed agent.
        environment.pop(MANAGED_AGENT, None)
        app = environment.get("CLOUD_AGENT_FLY_APP", "")
        token = environment.get("CLOUD_AGENT_FLY_TOKEN", "")
        cli = environment.get("CLOUD_AGENT_FLY_CLI", "fly")
        provider = environment.get("CLOUD_AGENT_SANDBOX_PROVIDER", "auto")
        if provider == "auto":
            provider = environment.get("SANDBOX_TYPE", "auto")
        desired = identity(app, token, cli) if provider == "fly" and app and token else None
        if self.configuration != desired or (self.process and self.process.poll() is not None):
            self.close()
        if desired is None:
            return
        if self.process is None:
            self.directory = create_directory()
            root = Path(self.directory.name)
            try:
                self.process = subprocess.Popen(
                    [cli, "agent", "run", str(root / "agent.log")],
                    env=isolated_environment(root, token),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 10
                while not (root / "agent.sock").exists():
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Local Fly agent failed to start")
                    time.sleep(0.02)
                descriptor = root / "owner.json"
                descriptor.write_text(json.dumps({"pid": os.getpid(), "identity": desired}))
                descriptor.chmod(0o600)
                self.configuration = desired
            except BaseException:
                self.close()
                raise
        environment[MANAGED_AGENT] = self.directory.name

    def close(self):
        try:
            if self.process:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        finally:
            if self.directory:
                self.directory.cleanup()
            self.directory = self.process = self.configuration = None
