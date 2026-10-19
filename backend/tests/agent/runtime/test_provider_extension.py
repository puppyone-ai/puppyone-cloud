"""A supplier registered only in providers works through unchanged workspace code."""

import asyncio
import sys
from types import ModuleType
from uuid import uuid4

import pytest

from src.config import settings
from src.platform.scope_sandbox.execution.providers import registry
from src.platform.scope_sandbox.execution.providers.port import (
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeTransition,
    resource_identity,
)
from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannel, WorkbenchChannels


@pytest.mark.asyncio
async def test_new_supplier_only_needs_provider_registration(monkeypatch):
    calls = []

    class Supplier:
        name = "third"
        capabilities = RuntimeCapabilities(
            recovery_backend=None, file_retention="volume", preserves_processes=False
        )

        def initialize(self, host):
            pass

        async def running_connection(self, host):
            calls.append("inspect")
            return RuntimeConnection(resource_identity(host.resource), object())

        async def connect_task(self, host, resource, connection):
            connection.validate(resource)
            host.connection = connection

        async def open_channel(self, host):
            calls.append("open")
            host.reader = asyncio.create_task(asyncio.Event().wait())

        async def disconnect(self, host):
            pass

        async def close_channel(self, host):
            calls.append("close")

    module = ModuleType("test_supplier")
    module.Supplier = Supplier
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(registry.PROVIDERS, "third", (module.__name__, "Supplier"))
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "third")
    assert registry.runtime_provider().name == "third"
    row = dict(
        id=str(uuid4()),
        generation=str(uuid4()),
        project_id="project",
        compute_epoch=1,
        binding={"provider": "third", "artifact": "pinned"},
        resource={"resource_id": "owned"},
    )
    pool = WorkbenchChannels(
        lambda row, **options: WorkbenchChannel(
            row, store=InMemoryExecutionSessionStore(), **options
        )
    )
    try:
        for _ in range(10):
            async with pool.borrow(row) as host:
                assert host.runtime.name == "third"
        assert calls == ["inspect", "open"]
    finally:
        await pool.close()
    assert calls == ["inspect", "open", "close"]


def test_connection_rejects_changed_resource_but_allows_distinct_tasks():
    resource = {
        "provider": "third",
        "resource_id": "one",
        "project_id": "project",
        "workbench_id": "workspace",
        "workbench_generation": "generation",
    }
    connection = RuntimeConnection(resource_identity(resource))
    connection.validate({**resource, "task_execution_id": "another"})
    for field in ("provider", "resource_id", "project_id", "workbench_id", "workbench_generation"):
        with pytest.raises(PermissionError):
            connection.validate({**resource, field: "wrong"})


def test_preservation_receipt_distinguishes_files_from_processes():
    resource = {"provider": "third", "resource_id": "one"}
    disk = RuntimeTransition(resource_identity(resource), True, False)
    assert disk.receipt(resource) == {"files_retained": True, "processes_retained": False}
    with pytest.raises(RuntimeError):
        RuntimeTransition(resource_identity(resource), False, False).receipt(resource)
    with pytest.raises(RuntimeError):
        disk.receipt({**resource, "resource_id": "replacement"})
