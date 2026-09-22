from __future__ import annotations

import pytest

from src.platform.scope_sandbox.execution.service import SandboxService


class _Meter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start_session(self, *, audit_context):
        self.events.append("reserve")
        assert audit_context["run_id"] == "sandbox-session:session-1"
        return audit_context["run_id"]

    async def finish_session(self, run_id: str):
        self.events.append("settle")

    async def cancel_session(self, run_id: str):
        self.events.append("cancel")


class _Sandbox:
    def __init__(self, events: list[str], *, start_success: bool = True) -> None:
        self.events = events
        self.start_success = start_success

    async def start(self, session_id, data, readonly):
        self.events.append("provider_start")
        return {"success": self.start_success}

    async def start_with_files(self, session_id, files, readonly, s3_service):
        return await self.start(session_id, files, readonly)

    async def exec(self, session_id, command):
        self.events.append("provider_exec")
        return {"success": True, "output": "ok"}

    async def stop(self, session_id):
        self.events.append("provider_stop")
        return {"success": True}

    async def stop_all(self):
        return None


@pytest.mark.asyncio
async def test_full_sandbox_lifecycle_reserves_before_provider_and_settles_after_stop(
    monkeypatch,
) -> None:
    from src.platform.billing import runtime as runtime_module

    events: list[str] = []
    monkeypatch.setattr(runtime_module, "get_runtime_metering_service", lambda: _Meter(events))
    service = SandboxService(sandbox_impl=_Sandbox(events))

    await service.start(
        "session-1",
        {},
        False,
        audit_context={"org_id": "org-1", "source": "automation"},
    )
    await service.exec("session-1", "echo ok")
    await service.stop("session-1")

    assert events == [
        "reserve",
        "provider_start",
        "provider_exec",
        "provider_stop",
        "settle",
    ]


@pytest.mark.asyncio
async def test_provider_start_failure_releases_reservation(monkeypatch) -> None:
    from src.platform.billing import runtime as runtime_module

    events: list[str] = []
    monkeypatch.setattr(runtime_module, "get_runtime_metering_service", lambda: _Meter(events))
    service = SandboxService(sandbox_impl=_Sandbox(events, start_success=False))

    result = await service.start(
        "session-1",
        {},
        False,
        audit_context={"org_id": "org-1", "source": "automation"},
    )

    assert not result["success"]
    assert events == ["reserve", "provider_start", "cancel"]
