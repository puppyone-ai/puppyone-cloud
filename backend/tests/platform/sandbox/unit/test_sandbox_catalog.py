"""Sandbox Profiles and Features are one registry, read by every layer.

A Profile is a machine shape (image per provider, compute, Features); a Feature
is an optional capability the client may offer. The owner's ``desktop`` choice
selects the Profile of the next computer; bindings written before Profiles
behave as ``base``; runtime toggles such as ``screen_stream`` withdraw access
without changing what the machine runs. The image-side declaration in
agent-worker/sandbox-profiles.json must agree with the catalog.
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
    enabled_features,
    profile_artifact,
    resolve_features,
    settings_profile,
    validate_settings,
)
from src.platform.workspace.lifecycle import new_binding

IMAGE_PROFILES = Path(__file__).resolve().parents[4] / "agent-worker" / "sandbox-profiles.json"
DIGEST = "registry.fly.io/app@sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def neutral_policy(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROFILE", "base")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_DISABLED_FEATURES", "")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES", "")


def provider(name="fly"):
    return SimpleNamespace(name=name, artifact=Mock(return_value="base-image"))


def reasons(items):
    return {item["id"]: item["reason"] for item in items}


def test_profiles_and_dependencies_name_only_registered_features():
    for item in PROFILES.values():
        assert set(item.features) <= set(FEATURES), item.id
    for feature in FEATURES.values():
        assert set(feature.requires) <= set(FEATURES), feature.id


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


def test_the_install_choice_selects_the_next_profile(monkeypatch):
    assert settings_profile({}).id == "base"
    assert settings_profile({"desktop": True}).id == "desktop"
    assert settings_profile({"desktop": False}).id == "base"
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROFILE", "desktop")
    assert settings_profile(None).id == "desktop"
    assert settings_profile({"desktop": False}).id == "base"


def test_settings_are_a_closed_set_of_booleans():
    assert validate_settings({"desktop": True, "screen_stream": False}) == {
        "desktop": True,
        "screen_stream": False,
    }
    for invalid in ({"browser": True}, {"desktop": "yes"}, ["desktop"]):
        with pytest.raises(ValueError):
            validate_settings(invalid)


def test_new_binding_follows_the_owner_choice(monkeypatch):
    assert new_binding(provider()) == {"provider": "fly", "profile": "base", "artifact": "base-image"}
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_DESKTOP_IMAGE", DIGEST)
    chosen = new_binding(provider(), {"sandbox_settings": {"desktop": True}})
    assert chosen == {"provider": "fly", "profile": "desktop", "artifact": DIGEST}


def test_desktop_profile_fails_closed_without_its_image(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_DESKTOP_IMAGE", "")
    with pytest.raises(ValueError):
        new_binding(provider(), {"sandbox_settings": {"desktop": True}})
    with pytest.raises(ValueError):
        profile_artifact(PROFILES["desktop"], provider("sprites"))
    assert PROFILES["desktop"].guest.as_dict() == {"cpus": 2, "memory_mb": 4096}


def test_base_reports_every_feature_as_not_installed():
    assert reasons(resolve_features(PROFILES["base"])) == {
        "desktop": "not_installed",
        "screen_stream": "not_installed",
        "browser": "not_installed",
    }
    assert enabled_features(PROFILES["base"]) == ()


def test_owner_toggle_withdraws_the_stream_but_not_the_machine_service():
    desktop = PROFILES["desktop"]
    resolved = resolve_features(desktop, {"screen_stream": False})
    assert reasons(resolved) == {"desktop": None, "screen_stream": "user_disabled", "browser": None}
    stream = next(item for item in resolved if item["id"] == "screen_stream")
    assert stream["surface"] == "screen" and stream["transport"] == "vnc"
    assert enabled_features(desktop) == ("desktop", "screen_stream", "browser")


def test_deployment_switch_and_dependencies(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_DISABLED_FEATURES", " desktop ,")
    desktop = PROFILES["desktop"]
    assert reasons(resolve_features(desktop)) == {
        "desktop": "disabled",
        "screen_stream": "requires",
        "browser": "requires",
    }
    assert enabled_features(desktop) == ()


def test_entitlement_policy_is_supplied_by_the_caller(monkeypatch):
    monkeypatch.setattr(
        "src.platform.sandbox.catalog.FEATURES",
        {
            **FEATURES,
            "desktop": FEATURES["desktop"].__class__("desktop", "desktop", entitlement="plan"),
        },
    )
    denied = resolve_features(PROFILES["desktop"], entitled=lambda key: False)
    assert reasons(denied)["desktop"] == "not_entitled"
    assert reasons(denied)["screen_stream"] == "requires"


def test_client_view_reports_current_and_next_profile(monkeypatch):
    unbound = public_sandbox({"state": "retired", "binding": {"profile": "desktop"}})
    assert unbound["profile"] == "base" and unbound["next_profile"] == "base"
    bound = public_sandbox(
        {
            "state": "paused",
            "binding": {"provider": "fly", "profile": "desktop", "artifact": "x"},
            "sandbox_settings": {"desktop": False},
        }
    )
    assert bound["profile"] == "desktop" and bound["next_profile"] == "base"
    assert reasons(bound["features"])["screen_stream"] is None
    unknown = public_sandbox({"state": "running", "binding": {"profile": "from-newer-release"}})
    assert unknown["profile"] == "from-newer-release" and unknown["features"] == []


def test_preview_features_reach_the_client_but_never_the_machine(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES", "screen_stream")
    base = PROFILES["base"]
    stream = next(item for item in resolve_features(base) if item["id"] == "screen_stream")
    # A preview stands in for its dependencies too: layout only, nothing behind it.
    assert stream["preview"] and stream["enabled"] and not stream["installed"]
    assert enabled_features(base) == ()
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES", "unknown")
    with pytest.raises(ValueError):
        resolve_features(base)
