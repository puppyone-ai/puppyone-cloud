from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.platform.workspace.execution import preflight


@pytest.fixture
def dependencies(monkeypatch):
    provider = SimpleNamespace(
        name="fly",
        artifact=Mock(return_value="pinned"),
        capabilities=SimpleNamespace(shared_workspace=True, native_files=True),
        preflight=AsyncMock(),
        aclose=AsyncMock(),
    )
    authorization = Mock()
    workspace = Mock()
    monkeypatch.setattr(preflight, "runtime_provider", lambda: provider)
    monkeypatch.setattr(preflight, "AuthorizationRepository", lambda: authorization)
    monkeypatch.setattr(preflight, "WorkbenchRepository", lambda: workspace)
    return provider, authorization, workspace


@pytest.mark.asyncio
async def test_preflight_proves_provider_and_both_database_contracts_without_writes(dependencies):
    provider, authorization, workspace = dependencies
    result = await preflight.check()
    assert result["provider"] == "fly"
    absent = "00000000-0000-0000-0000-000000000000"
    authorization.load_project_facts.assert_called_once_with(absent, absent)
    # Probes this deployment's own pool: never another deployment's queue.
    workspace.pending.assert_called_once_with("fly", preflight.settings.AGENT_WORKER_POOL)
    workspace.verify_opening_contract.assert_called_once_with()
    workspace.request_open.assert_not_called()
    provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    [
        "provider_connection",
        "project_authorization_database",
        "workspace_database",
        "workspace_readiness_database",
    ],
)
async def test_preflight_names_the_failing_boundary_without_sdk_text(dependencies, stage):
    provider, authorization, workspace = dependencies
    operation = {
        "provider_connection": provider.preflight,
        "project_authorization_database": authorization.load_project_facts,
        "workspace_database": workspace.pending,
        "workspace_readiness_database": workspace.verify_opening_contract,
    }[stage]
    operation.side_effect = httpx.ConnectError("https://secret@host/?private=customer")
    with pytest.raises(preflight.PreflightFailure) as caught:
        await preflight.check()
    assert caught.value.report["stage"] == stage
    assert caught.value.report["error_type"] == "ConnectError"
    assert "secret" not in str(caught.value.report)
    if stage == "provider_connection":
        authorization.load_project_facts.assert_not_called()
        workspace.pending.assert_not_called()
    provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_fails", [False, True])
async def test_cleanup_failure_is_safe_and_does_not_mask_original_failure(
    dependencies, connection_fails
):
    provider, _, _ = dependencies
    provider.aclose.side_effect = RuntimeError("private cleanup details")
    if connection_fails:
        provider.preflight.side_effect = httpx.ConnectError("private connection details")
    with pytest.raises(preflight.PreflightFailure) as caught:
        await preflight.check()
    assert caught.value.report["stage"] == (
        "provider_connection" if connection_fails else "provider_cleanup"
    )
    assert "private" not in str(caught.value.report)
    if connection_fails:
        assert caught.value.report["error_type"] == "ConnectError"
        assert caught.value.report["cleanup_failure"]["error_type"] == "RuntimeError"


def test_preflight_cli_returns_structured_failure_and_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight,
        "check",
        AsyncMock(
            side_effect=preflight.PreflightFailure(
                "provider_connection", httpx.ConnectError("secret")
            )
        ),
    )
    with pytest.raises(SystemExit) as caught:
        preflight.main()
    assert caught.value.code == 1
    output = capsys.readouterr().err
    assert '"stage": "provider_connection"' in output
    assert "secret" not in output
