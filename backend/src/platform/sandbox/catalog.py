"""Sandbox Profiles and Features: what a workspace machine provides, not where it runs.

A Feature is an optional capability beyond the baseline runtime protocol. A
Profile is one machine shape: image per provider, compute, extra persisted
paths and the Features it ships. New workspaces pin a Profile in their binding;
bindings without one are ``base``.

Each workspace carries user settings: ``desktop`` chooses the Profile of the
next computer (an existing binding never changes), and runtime toggles such as
``screen_stream`` withdraw an installed Feature without rebuilding the machine.

This module is data plus pure functions. It never imports Workspace, Access,
Agent or entitlement code; callers pass policy in. The image-side declaration
is ``agent-worker/sandbox-profiles.json`` and a test keeps both in agreement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from src.config import settings

BASE_PROFILE = "base"


@dataclass(frozen=True)
class SandboxFeature:
    id: str
    # Capability name the running image must declare in runtime-manifest.json.
    runtime_capability: str
    # Client entry kind (for example "screen"); None for capabilities without UI.
    surface: str | None = None
    # Wire format of a streamed surface; the client picks its viewer by it.
    transport: str | None = None
    # Features that must be available for this one to be.
    requires: tuple[str, ...] = ()
    # Organization entitlement feature key; None means not plan-gated.
    entitlement: str | None = None
    # Whether the workspace owner may switch it off without rebuilding the machine.
    user_toggle: bool = False


@dataclass(frozen=True)
class MachineGuest:
    cpus: int
    memory_mb: int

    def as_dict(self) -> dict:
        return {"cpus": self.cpus, "memory_mb": self.memory_mb}


@dataclass(frozen=True)
class SandboxProfile:
    id: str
    features: tuple[str, ...]
    guest: MachineGuest
    # Paths besides /workspace kept on the persistent disk.
    persist_paths: tuple[str, ...] = ()
    # Provider name -> settings attribute holding the pinned image. Empty for
    # ``base``, which uses each provider's default artifact.
    images: Mapping[str, str] = field(default_factory=dict)


FEATURES: Mapping[str, SandboxFeature] = {
    feature.id: feature
    for feature in (
        SandboxFeature("desktop", "desktop"),
        SandboxFeature(
            "screen_stream",
            "screen_stream",
            surface="screen",
            transport="vnc",
            requires=("desktop",),
            user_toggle=True,
        ),
        SandboxFeature("browser", "browser", requires=("desktop",)),
    )
}

PROFILES: Mapping[str, SandboxProfile] = {
    profile.id: profile
    for profile in (
        SandboxProfile(BASE_PROFILE, (), MachineGuest(cpus=1, memory_mb=1024)),
        SandboxProfile(
            "desktop",
            ("desktop", "screen_stream", "browser"),
            MachineGuest(cpus=2, memory_mb=4096),
            persist_paths=("/home/node",),
            images={"fly": "CLOUD_AGENT_FLY_DESKTOP_IMAGE", "docker": "CLOUD_AGENT_DESKTOP_IMAGE"},
        ),
    )
}

# Workspace settings: the install choice plus each Feature's user toggle.
SETTING_KEYS = frozenset({"desktop", *(f.id for f in FEATURES.values() if f.user_toggle)})


def profile(name: str | None) -> SandboxProfile:
    """The Profile a binding names; bindings written before Profiles are ``base``."""
    selected = name or BASE_PROFILE
    if selected not in PROFILES:
        raise ValueError(f"Unsupported sandbox profile: {selected}")
    return PROFILES[selected]


def binding_profile(binding: Mapping | None) -> SandboxProfile:
    return profile((binding or {}).get("profile"))


def default_profile() -> SandboxProfile:
    return profile(settings.CLOUD_AGENT_SANDBOX_PROFILE)


def validate_settings(value: Mapping) -> dict:
    """Workspace settings are a closed set of booleans."""
    if not isinstance(value, Mapping) or set(value) - SETTING_KEYS:
        raise ValueError("Unknown sandbox setting")
    if not all(isinstance(item, bool) for item in value.values()):
        raise ValueError("Sandbox settings are booleans")
    return dict(value)


def settings_profile(value: Mapping | None) -> SandboxProfile:
    """Profile a new computer receives: the owner's install choice, else the default."""
    choice = (value or {}).get("desktop")
    if choice is None:
        return default_profile()
    return PROFILES["desktop"] if choice else PROFILES[BASE_PROFILE]


def profile_artifact(selected: SandboxProfile, provider) -> str:
    """Pinned image for a new binding; ValueError when this deployment lacks it."""
    if selected.id == BASE_PROFILE:
        return provider.artifact(settings.CLOUD_AGENT_IMAGE)
    setting = selected.images.get(provider.name)
    artifact = getattr(settings, setting, "") if setting else ""
    if not artifact:
        raise ValueError(f"Sandbox profile {selected.id} has no {provider.name} image")
    return artifact


def disabled_features() -> frozenset[str]:
    return frozenset(
        name.strip()
        for name in settings.CLOUD_AGENT_SANDBOX_DISABLED_FEATURES.split(",")
        if name.strip()
    )


def preview_features() -> tuple[str, ...]:
    names = [
        name.strip()
        for name in settings.CLOUD_AGENT_SANDBOX_PREVIEW_FEATURES.split(",")
        if name.strip()
    ]
    unknown = set(names) - set(FEATURES)
    if unknown:
        raise ValueError(f"Unknown sandbox preview features: {sorted(unknown)}")
    return tuple(dict.fromkeys(names))


def resolve_features(
    selected: SandboxProfile,
    user_settings: Mapping | None = None,
    entitled: Callable[[str], bool] | None = None,
) -> list[dict]:
    """Policy view of every registered Feature for one bound Profile. It never
    contacts the machine; using a Feature still requires the running image to
    declare its runtime capability.

    Reasons, first match wins: ``not_installed`` (the Profile lacks it),
    ``disabled`` (deployment switch), ``not_entitled``, ``user_disabled``
    (workspace toggle) and ``requires`` (a required Feature is unavailable).
    Preview Features are reported for client layout work on Profiles that do
    not ship them; the machine has nothing behind them.
    """
    disabled = disabled_features()
    previews = set(preview_features()) - set(selected.features)
    choices = user_settings or {}
    reasons = {}
    for name, feature in FEATURES.items():
        reason = None
        if name not in selected.features and name not in previews:
            reason = "not_installed"
        elif name in disabled:
            reason = "disabled"
        elif feature.entitlement and entitled is not None and not entitled(feature.entitlement):
            reason = "not_entitled"
        elif feature.user_toggle and choices.get(name) is False:
            reason = "user_disabled"
        elif name not in previews and any(reasons.get(r) for r in feature.requires):
            reason = "requires"
        reasons[name] = reason
    return [
        {
            "id": name,
            "surface": feature.surface,
            "transport": feature.transport,
            "installed": name in selected.features,
            "enabled": reasons[name] is None,
            "reason": reasons[name],
            "preview": name in previews,
        }
        for name, feature in FEATURES.items()
    ]


def enabled_features(selected: SandboxProfile, entitled=None) -> tuple[str, ...]:
    """Features the machine starts services for. Owner toggles only withdraw
    access, so they never change what the machine runs; previews never reach it.
    """
    return tuple(
        item["id"]
        for item in resolve_features(selected, None, entitled)
        if item["enabled"] and item["installed"]
    )
