"""ScopeSyncService policy-resolution tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.platform.scope_sync.service import ScopeSyncService
from src.platform.scope_sync.settings_store import InMemorySettingsStore


@dataclass
class _Scope:
    id: str
    project_id: str
    path: str


SUB = _Scope(id="s-sub", project_id="p1", path="docs")
_LOOKUP = {SUB.id: SUB}


def _svc():
    return ScopeSyncService(
        scope_lookup=lambda sid: _LOOKUP.get(sid),
        settings_store=InMemorySettingsStore(),
    )


def test_sub_scope_dev_gets_dev_policy():
    out = _svc().resolve_policy(project_id="p1", scope_id="s-sub", persona="dev")
    assert out["scope_role"] == "sub" and out["persona"] == "dev"
    assert out["policy"]["quiescence_publish_s"] == 0           # dev publishes deliberately
    assert out["policy"]["publish_on_verification"] is True


def test_sub_scope_non_dev_gets_autopilot():
    out = _svc().resolve_policy(project_id="p1", scope_id="s-sub", persona="non_dev")
    assert out["policy"]["conflict_policy"] == "agent_auto_resolve"
    assert out["policy"]["publish_on_disconnect"] is True


def test_invalid_persona_defaults_dev():
    out = _svc().resolve_policy(project_id="p1", scope_id="s-sub", persona="wizard")
    assert out["persona"] == "dev"


def test_settings_default_then_set_then_policy_uses_stored_persona():
    svc = _svc()
    # default
    assert svc.get_settings(project_id="p1", scope_id="s-sub") == {"persona": "dev", "auto_sync": True}
    # set persona → non_dev, auto_sync off
    svc.set_settings(project_id="p1", scope_id="s-sub", persona="non_dev", auto_sync=False)
    assert svc.get_settings(project_id="p1", scope_id="s-sub") == {"persona": "non_dev", "auto_sync": False}
    # resolve_policy with NO explicit persona now uses the stored one
    out = svc.resolve_policy(project_id="p1", scope_id="s-sub")
    assert out["persona"] == "non_dev" and out["auto_sync"] is False
    # explicit override still wins
    assert svc.resolve_policy(project_id="p1", scope_id="s-sub", persona="dev")["persona"] == "dev"


def test_set_settings_invalid_persona_normalized_to_dev():
    svc = _svc()
    assert svc.set_settings(project_id="p1", scope_id="s-sub", persona="wizard")["persona"] == "dev"


def test_unknown_scope_raises():
    with pytest.raises(LookupError):
        _svc().resolve_policy(project_id="p1", scope_id="ghost")


def test_wrong_project_raises():
    with pytest.raises(LookupError):
        _svc().resolve_policy(project_id="OTHER", scope_id="s-sub")
