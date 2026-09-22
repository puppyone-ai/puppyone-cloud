"""M-3 — Scheduler must re-resolve principal access at execution time.

The vulnerability: scheduled agent jobs could fall back to project.created_by
as the execution principal. If that user later left the org, jobs would still execute
with their (formerly granted) permissions.

Fix: at the start of each execution, verify the persisted user STILL has
project access. If not, abort with status="failed" and a clear reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.infra.scheduler.jobs.agent_job import _execute_agent_task_async
from tests.authorization_fakes import authorization_for


def _stub_supabase_with_agent_row(row: dict):
    """Patch SupabaseClient so the initial agent fetch returns `row`."""
    sb = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.single.return_value = chain
    chain.execute.return_value = MagicMock(data=row)
    sb.client.table.return_value = chain
    return sb


@pytest.mark.asyncio
async def test_principal_no_longer_member_aborts_with_principal_invalid():
    """Stale created_by who lost project access ⇒ refuse to run."""
    agent_row = {
        "id": "agent-1",
        "project_id": "proj-1",
        "created_by": "ex-employee",  # left the org
        "config": {
            "name": "X",
            "type": "schedule",
            "task_content": "do stuff",
            "trigger": {"type": "cron", "config": {}},
        },
        "project": {"created_by": "ex-employee", "org_id": "org-1"},
    }
    sb = _stub_supabase_with_agent_row(agent_row)

    with patch(
        "src.infra.supabase.client.SupabaseClient", return_value=sb,
    ), patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization_for(),
    ):

        result = await _execute_agent_task_async("agent-1")

    assert result["status"] == "failed"
    assert "principal_invalid" in result.get("error", "")


@pytest.mark.asyncio
async def test_principal_access_check_error_aborts():
    """If the verify call itself errors, fail closed — never silently run
    on a potentially-stale principal."""
    agent_row = {
        "id": "agent-1",
        "project_id": "proj-1",
        "created_by": "user-x",
        "config": {
            "name": "X",
            "type": "schedule",
            "task_content": "stuff",
            "trigger": {"type": "cron", "config": {}},
        },
        "project": {"created_by": "user-x", "org_id": "org-1"},
    }
    sb = _stub_supabase_with_agent_row(agent_row)
    authorization = MagicMock()
    authorization.allows.side_effect = RuntimeError("DB outage")

    with patch(
        "src.infra.supabase.client.SupabaseClient", return_value=sb,
    ), patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization,
    ):
        result = await _execute_agent_task_async("agent-1")

    assert result["status"] == "failed"
    assert "Principal access check failed" in result.get("error", "")


@pytest.mark.asyncio
async def test_no_user_id_at_all_aborts():
    """Empty access_surface.created_by AND empty project.created_by ⇒ refuse."""
    agent_row = {
        "id": "agent-1",
        "project_id": "proj-1",
        "created_by": None,
        "config": {
            "name": "X",
            "type": "schedule",
            "task_content": "stuff",
            "trigger": {"type": "cron", "config": {}},
        },
        "project": {"created_by": None, "org_id": "org-1"},
    }
    sb = _stub_supabase_with_agent_row(agent_row)

    with patch("src.infra.supabase.client.SupabaseClient", return_value=sb):
        result = await _execute_agent_task_async("agent-1")

    assert result["status"] == "failed"
    assert "no associated user" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_agent_owner_preferred_over_project_creator():
    """Agent access-surface created_by wins over project.created_by —
    that's the natural impersonation principal for the agent."""
    agent_row = {
        "id": "agent-1",
        "project_id": "proj-1",
        "created_by": "agent-owner",
        "config": {
            "name": "X",
            "type": "schedule",
            "task_content": "x",
            "trigger": {"type": "cron", "config": {}},
        },
        "project": {"created_by": "different-creator", "org_id": "org-1"},
    }
    sb = _stub_supabase_with_agent_row(agent_row)

    captured_user = {}

    def fake_verify(project_id, user_id, action):
        captured_user["uid"] = user_id
        return False  # short-circuit so we don't run the real task

    authorization = MagicMock()
    authorization.allows.side_effect = fake_verify

    with patch(
        "src.infra.supabase.client.SupabaseClient", return_value=sb,
    ), patch(
        "src.platform.authorization.factory.build_authorization_service",
        return_value=authorization,
    ):
        await _execute_agent_task_async("agent-1")

    assert captured_user["uid"] == "agent-owner", (
        "Scheduler must use the agent's own owner, not the project creator,"
        " as the impersonation principal."
    )
