"""Read-only release check for the configured workspace provider and SQL contract.

Run in both API and worker release environments, with their actual configuration.
The runtime handshake separately validates the installed VM artifact before use.
"""

import asyncio
import json
import sys

from src.config import settings
from src.infra.dependency_diagnostics import dependency_failure, network_environment
from src.platform.authorization.repository import AuthorizationRepository

from ..repository import WorkbenchRepository
from .providers.registry import runtime_provider
from .runtime_contract import FILE_PROTOCOL, RUNTIME_PROTOCOL


class PreflightFailure(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        self.report = {"status": "failed", "stage": stage, **dependency_failure(cause, stage)}
        super().__init__("workspace_preflight_failed")


async def check():
    provider = None
    failure = None
    stage = "provider_configuration"
    try:
        provider = runtime_provider()
        artifact = provider.artifact(settings.CLOUD_AGENT_IMAGE)
        if not provider.capabilities.shared_workspace or not provider.capabilities.native_files:
            raise ValueError("Configured provider does not support hosted workspaces")
        async with asyncio.timeout(25):
            stage = "provider_connection"
            await provider.preflight(artifact)
            stage = "project_authorization_database"
            # Read an intentionally absent identity to prove the exact RPC and
            # transport used by project verification, without granting access.
            absent = "00000000-0000-0000-0000-000000000000"
            await asyncio.to_thread(AuthorizationRepository().load_project_facts, absent, absent)
            stage = "workspace_database"
            # This RPC exists only after the durable-open migration. It does not
            # claim or wake any workspace and is safe in a release health probe.
            await asyncio.to_thread(WorkbenchRepository().pending, provider.name)
            stage = "workspace_readiness_database"
            # Null revision fails before row locks or writes. Missing functions,
            # changed signatures and transport failures must block activation.
            await asyncio.to_thread(WorkbenchRepository().verify_opening_contract)
        return dict(
            provider=provider.name,
            artifact=artifact,
            runtime_protocol=RUNTIME_PROTOCOL,
            file_protocol=FILE_PROTOCOL,
            network_environment=network_environment(),
        )
    except Exception as exc:
        failure = PreflightFailure(stage, exc)
        raise failure from exc
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception as exc:
                if failure is None:
                    raise PreflightFailure("provider_cleanup", exc) from exc
                failure.report["cleanup_failure"] = dependency_failure(exc, "provider_cleanup")


def main():
    try:
        print(json.dumps(asyncio.run(check())))
    except PreflightFailure as exc:
        print(json.dumps(exc.report), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
