"""Provider factory / selection tests (the user-selectable two versions)."""

from __future__ import annotations

import types

import pytest

from src.platform.workspace.scopes.e2b_provider import E2BProvider
from src.platform.workspace.scopes.factory import build_provider, provider_from_settings
from src.platform.workspace.scopes.fly_provider import FlyMachinesProvider


def test_build_fly_provider():
    prov = build_provider("fly", fly_app="app", fly_token="tok", fly_image="img")
    assert isinstance(prov, FlyMachinesProvider)
    assert prov.capabilities().name == "fly"


def test_build_e2b_provider_with_injected_client():
    class _Client:  # satisfies E2BClient structurally; never called here
        def create(self, spec): return "x"
        def pause(self, sid): ...
        def resume(self, sid): ...
        def kill(self, sid): ...
        def get_state(self, sid): ...

    prov = build_provider("e2b", e2b_client=_Client())
    assert isinstance(prov, E2BProvider)
    assert prov.capabilities().name == "e2b"


def test_build_e2b_defaults_to_sdk_client_without_network():
    # Constructing the SDK-backed client must not touch the network/SDK.
    prov = build_provider("e2b", e2b_api_key="k")
    assert isinstance(prov, E2BProvider)


def test_build_e2b_passes_custom_template_to_sdk_client():
    # roadmap #6: the configured template id flows into the SDK client (used as
    # Sandbox.create(template, …)); unset → default ("").
    prov = build_provider("e2b", e2b_api_key="k", e2b_template="puppyone-tmpl-123")
    assert prov._client._template == "puppyone-tmpl-123"
    assert build_provider("e2b", e2b_api_key="k")._client._template == ""


def test_fly_requires_app_and_token():
    with pytest.raises(ValueError):
        build_provider("fly", fly_app="", fly_token="")


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_provider("nope")


def test_build_session_store_memory():
    from src.platform.workspace.scopes.factory import build_session_store
    from src.platform.workspace.scopes.registry import InMemorySandboxSessionStore
    assert isinstance(build_session_store("memory"), InMemorySandboxSessionStore)


def test_build_session_store_unknown_raises():
    from src.platform.workspace.scopes.factory import build_session_store
    with pytest.raises(ValueError):
        build_session_store("redis")


def test_provider_from_settings_picks_configured_default():
    settings = types.SimpleNamespace(
        SCOPE_SANDBOX_PROVIDER="fly",
        SCOPE_SANDBOX_FLY_APP="app",
        SCOPE_SANDBOX_FLY_TOKEN="tok",
        SCOPE_SANDBOX_FLY_IMAGE="img",
        E2B_API_KEY=None,
        SCOPE_SANDBOX_E2B_TEMPLATE="tmpl-xyz",
    )
    assert isinstance(provider_from_settings(settings), FlyMachinesProvider)
    # explicit override wins, and the configured E2B template flows through
    e2b = provider_from_settings(settings, name="e2b")
    assert isinstance(e2b, E2BProvider) and e2b._client._template == "tmpl-xyz"
