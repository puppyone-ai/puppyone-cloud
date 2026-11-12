"""Sandbox Profiles and Features: what a workspace machine provides, not where it runs.

A Feature is an optional capability beyond the baseline runtime protocol. A
Profile is one machine shape: image per provider, compute, extra persisted
paths and the Features it ships. New workspaces pin a Profile in their binding;
bindings without one are ``base``.

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
    # Organization entitlement feature key; None means not plan-gated.
    entitlement: str | None = None


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
        SandboxFeature(
            "desktop_screen", "desktop_screen", surface="screen", entitlement="cloud_desktop"
        ),
        SandboxFeature("browser", "browser", entitlement="cloud_desktop"),
    )
}

PROFILES: Mapping[str, SandboxProfile] = {
    profile.id: profile
    for profile in (
        SandboxProfile(BASE_PROFILE, (), MachineGuest(cpus=1, memory_mb=1024)),
        SandboxProfile(
            "desktop",
            ("desktop_screen", "browser"),
            MachineGuest(cpus=2, memory_mb=4096),
            persist_paths=("/home/node",),
            images={"fly": "CLOUD_AGENT_FLY_DESKTOP_IMAGE"},
        ),
    )
}


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


def resolve_features(
    selected: SandboxProfile, entitled: Callable[[str], bool] | None = None
) -> list[dict]:
    """Policy view of a Profile's Features. It never contacts the machine; using a
    Feature still requires the running image to declare its runtime capability.
    """
    disabled = disabled_features()
    resolved = []
    for name in selected.features:
        feature = FEATURES[name]
        reason = None
        if name in disabled:
            reason = "disabled"
        elif feature.entitlement and entitled is not None and not entitled(feature.entitlement):
            reason = "not_entitled"
        resolved.append(
            {
                "id": name,
                "surface": feature.surface,
                "enabled": reason is None,
                "reason": reason,
            }
        )
    return resolved


def enabled_features(selected: SandboxProfile, entitled=None) -> tuple[str, ...]:
    return tuple(item["id"] for item in resolve_features(selected, entitled) if item["enabled"])

