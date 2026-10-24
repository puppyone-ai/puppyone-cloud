"""Routing contract between the admitting API and the workers that execute Runs.

An API admits a Run into its worker pool with the current runtime contract. A
worker claims only Runs of its own pool whose contract it implements, so a
local stack sharing a hosted database and an older deployed worker never take
each other's work.

Bump RUN_CONTRACT only when an older worker could not execute a newly admitted
Run correctly. Deploy workers that support the new value before any API that
admits it; until then new Runs wait instead of failing.
"""

import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.config import settings

RUN_CONTRACT = 1
SUPPORTED_RUN_CONTRACTS = (1, 1)

# Workers whose configuration cannot serve a Run each claim it at most once.
# The MAX_DISPATCH_ATTEMPTS-th such worker ends an active Run as
# no_capable_worker instead of releasing it; terminal cleanup is never abandoned.
MAX_DISPATCH_ATTEMPTS = 5

# Sandbox codes meaning "this worker's configuration cannot serve the binding";
# another worker of the same pool may. Every other failure belongs to the task.
INCAPABLE_CODES = frozenset({"workbench_provider_not_configured", "workbench_protocol_mismatch"})


def run_dispatch() -> dict:
    """The route an API records on each Run it admits."""
    return {"pool": settings.AGENT_WORKER_POOL, "contract": RUN_CONTRACT}


@dataclass(frozen=True)
class WorkerIdentity:
    """Who executed an attempt. Stored on the execution; a subset is user-visible."""

    id: str
    pool: str
    service: str
    environment: str
    host: str
    build: str
    provider: str
    contracts: tuple[int, int] = SUPPORTED_RUN_CONTRACTS

    def claim(self) -> dict:
        return {
            "id": self.id,
            "pool": self.pool,
            "contracts": list(self.contracts),
            "service": self.service,
            "environment": self.environment,
            "host": self.host,
            "build": self.build,
            "provider": self.provider,
        }

    def public(self) -> dict:
        # Run events are visible to the Run's user: no host or environment names.
        return {"id": self.id, "pool": self.pool, "build": self.build}


def _bounded(value: str) -> str:
    return (value or "unknown").strip()[:128] or "unknown"


def _build() -> str:
    for key in ("RAILWAY_GIT_COMMIT_SHA", "PUPPYONE_BUILD_ID"):
        if value := os.environ.get(key, "").strip():
            return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=Path(__file__).parent,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def worker_identity(provider: str) -> WorkerIdentity:
    """A new identity per process; a restart is a different worker."""
    return WorkerIdentity(
        id=str(uuid4()),
        pool=settings.AGENT_WORKER_POOL,
        service=_bounded(os.environ.get("SERVICE_ROLE") or "agent_worker"),
        environment=_bounded(os.environ.get("RAILWAY_ENVIRONMENT_NAME") or settings.APP_ENV),
        host=_bounded(os.environ.get("RAILWAY_REPLICA_ID") or socket.gethostname()),
        build=_bounded(_build()),
        provider=_bounded(provider),
    )
