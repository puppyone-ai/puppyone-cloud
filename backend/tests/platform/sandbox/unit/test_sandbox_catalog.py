"""Sandbox Profiles and Features are one registry, read by every layer.

A Profile is a machine shape (image per provider, compute, Features); a Feature
is an optional capability the client may offer. New workspaces pin the default
Profile; bindings written before Profiles behave as ``base``. The image-side
declaration in agent-worker/sandbox-profiles.json must agree with the catalog.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.config import settings
from src.platform.access.adapters.api.workbench_router import public_sandbox
from src.platform.sandbox import (
    FEATURES,
    PROFILES,
    binding_profile,
    default_profile,
    enabled_features,
    profile_artifact,
    resolve_features,
)
from src.platform.workspace.lifecycle import new_binding

IMAGE_PROFILES = Path(__file__).resolve().parents[4] / "agent-worker" / "sandbox-profiles.json"
DIGEST = "registry.fly.io/app@sha256:" + "a" * 64


def provider(name="fly"):
    return SimpleNamespace(name=name, artifact=Mock(return_value="base-image"))


def test_profiles_name_only_registered_features():
    for item in PROFILES.values():
        assert set(item.features) <= set(FEATURES), item.id


def test_image_declaration_matches_the_catalog():
    declared = json.loads(IMAGE_PROFILES.read_text())
    assert set(declared) == set(PROFILES)
    for name, item in PROFILES.items():
        expected = [FEATURES[feature].runtime_capability for feature in item.features]
        assert declared[name]["capabilities"] == expected, name


def test_bindings_without_a_profile_are_base():
    assert binding_profile({"provider": "fly", "artifact": "x"}).id == "base"
    assert binding_profile(None).id == "base"
    with pytest.raises(ValueError):
        binding_profile({"profile": "unknown"})


def test_new_binding_pins_the_default_profile(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROFILE", "base")
    assert new_binding(provider()) == {
        "provider": "fly",
        "profile": "base",
        "artifact": "base-image",
    }


def test_desktop_profile_uses_its_own_image_and_fails_closed_without_one(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROFILE", "desktop")
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_DESKTOP_IMAGE", "")
    with pytest.raises(ValueError):
        new_binding(provider())
    with pytest.raises(ValueError):
        profile_artifact(default_profile(), provider("sprites"))
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_DESKTOP_IMAGE", DIGEST)
    assert new_binding(provider())["artifact"] == DIGEST
    assert default_profile().guest.as_dict() == {"cpus": 2, "memory_mb": 4096}


def test_deployment_switch_disables_a_feature_everywhere(monkeypatch):
    desktop = PROFILES["desktop"]
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_DISABLED_FEATURES", " desktop_screen ,")
    assert resolve_features(desktop) == [
        {
            "id": "desktop_screen",
            "surface": "screen",
            "enabled": False,
            "reason": "disabled",
            "preview": False,
        },
        {"id": "browser", "surface": None, "enabled": True, "reason": None, "preview": False},
    ]
    assert enabled_features(desktop) == ("browser",)


def test_entitlement_policy_is_supplied_by_the_caller(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_DISABLED_FEATURES", "")
    denied = resolve_features(PROFILES["desktop"], entitled=lambda key: False)
    assert {item["reason"] for item in denied} == {"not_entitled"}
    assert all(item["enabled"] for item in resolve_features(PROFILES["desktop"]))


def test_client_view_of_base_and_unknown_profiles(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROFILE", "base")
    assert public_sandbox(None) == {"profile": "base", "features": []}
    assert public_sandbox({"provider": "fly", "artifact": "x"}) == {
        "profile": "base",
        "features": [],
    }
    assert public_sandbox({"profile": "from-newer-release"}) == {
        "profile": "from-newer-release",
        "features": [],
    }


def test_preview_features_reach_the_client_but_never_the_machine(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_DISABLED_FEATURES", "")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES", "desktop_screen")
    base = PROFILES["base"]
    assert resolve_features(base) == [
        {
            "id": "desktop_screen",
            "surface": "screen",
            "enabled": True,
            "reason": None,
            "preview": True,
        }
    ]
    assert enabled_features(base) == ()
    assert [item["preview"] for item in resolve_features(PROFILES["desktop"])] == [False, False]
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES", "unknown")
    with pytest.raises(ValueError):
        resolve_features(base)
