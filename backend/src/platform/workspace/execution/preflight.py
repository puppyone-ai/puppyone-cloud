"""Read-only release check for the configured workspace provider and SQL contract.

Run in both API and worker release environments, with their actual configuration.
The runtime handshake separately validates the installed VM artifact before use.
"""

import asyncio
import json

from src.config import settings

from ..repository import WorkbenchRepository
from .providers.registry import runtime_provider
from .runtime_contract import FILE_PROTOCOL, RUNTIME_PROTOCOL


async def check():
    provider = runtime_provider()
    artifact = provider.artifact(settings.CLOUD_AGENT_IMAGE)
    try:
        if not provider.capabilities.shared_workspace or not provider.capabilities.native_files:
            raise ValueError("Configured provider does not support hosted workspaces")
        async with asyncio.timeout(25):
            await provider.preflight(artifact)
            # This RPC exists only after the durable-open migration. It does not
            # claim or wake any workspace and is safe in a release health probe.
            await asyncio.to_thread(WorkbenchRepository().pending, provider.name)
        return dict(
            provider=provider.name,
            artifact=artifact,
            runtime_protocol=RUNTIME_PROTOCOL,
            file_protocol=FILE_PROTOCOL,
        )
    finally:
        await provider.aclose()


def main():
    try:
        print(json.dumps(asyncio.run(check())))
    except Exception as exc:
        # Provider/SQL SDK exceptions can contain credentials. Only safe labels
        # leave the process; the owning operator can inspect their service logs.
        raise SystemExit(getattr(exc, "code", "workspace_preflight_failed")) from None


if __name__ == "__main__":
    main()
