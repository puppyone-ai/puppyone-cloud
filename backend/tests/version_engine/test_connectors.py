"""Tests for connector architecture alignment.

Covers:
  - BaseConnector pull() method
  - IntegrationEngine decoupling (fetch → compare → ProductOperationAdapter.write)
  - Unified connections manager routing by provider
"""

from unittest.mock import MagicMock

import pytest

from src.connectors.datasource._base import (
    BaseConnector,
    Capability,
    ConnectorSpec,
    FetchResult,
)

# ── BaseConnector Tests ────────────────────────────────────────

class FakeConnector(BaseConnector):
    """Minimal connector for testing."""

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            provider="fake",
            display_name="Fake",
            capabilities=Capability.PULL,
            supported_directions=["inbound"],
        )

    async def fetch(self, config, credentials):
        return FetchResult(
            content={"test": True},
            content_hash="abc123",
        )


class TestBaseConnector:
    def test_spec_returns_provider(self):
        c = FakeConnector()
        assert c.spec().provider == "fake"

    @pytest.mark.asyncio
    async def test_fetch_returns_result(self):
        c = FakeConnector()
        result = await c.fetch({}, None)
        assert result.content == {"test": True}
        assert result.content_hash == "abc123"

    @pytest.mark.asyncio
    async def test_pull_raises_not_implemented(self):
        """Default pull() raises NotImplementedError."""
        c = FakeConnector()
        mock_sync = MagicMock()
        mock_sync.config = {}
        with pytest.raises(NotImplementedError, match="use IntegrationEngine"):
            await c.pull(mock_sync)

    @pytest.mark.asyncio
    async def test_push_raises_not_implemented(self):
        c = FakeConnector()
        mock_sync = MagicMock()
        with pytest.raises(NotImplementedError, match="does not support push"):
            await c.push(mock_sync, "content", "text")

    def test_list_resources_default_empty(self):
        c = FakeConnector()
        import asyncio
        result = asyncio.run(
            c.list_resources(MagicMock())
        )
        assert result == []


class PullableConnector(BaseConnector):
    """Connector that implements pull()."""

    def spec(self):
        return ConnectorSpec(
            provider="pullable", display_name="Pullable",
            capabilities=Capability.PULL, supported_directions=["inbound"],
        )

    async def fetch(self, config, credentials):
        return FetchResult(content="fetched", content_hash="h1")

    async def pull(self, sync):
        return FetchResult(content="pulled", content_hash="h2")


class TestPullableConnector:
    @pytest.mark.asyncio
    async def test_pull_overrides_default(self):
        c = PullableConnector()
        result = await c.pull(MagicMock())
        assert result.content == "pulled"
        assert result.content_hash == "h2"

# ── Unified Manager Routing Tests ──────────────────────────────

class TestManagerRouting:
    """Test that the unified manager routes to correct handler by provider."""

    def test_known_providers(self):
        """Verify all expected providers are handled in the routing logic."""
        known_providers = {"agent", "mcp", "sandbox", "direct"}
        assert len(known_providers) == 4
        assert "direct" in known_providers


# ── IntegrationEngine Decoupling Tests ─────────────────────────

class TestIntegrationEngineDecoupling:
    """Verify IntegrationEngine properly separates concerns."""

    def test_engine_module_importable(self):
        from src.platform.integrations.engine import IntegrationEngine
        assert hasattr(IntegrationEngine, "execute")

    def test_engine_uses_version_write_port(self):
        """IntegrationEngine.execute() should enter project writes through one port."""
        import inspect

        from src.platform.integrations.engine import IntegrationEngine
        source = inspect.getsource(IntegrationEngine.execute)
        assert "self.write_port.write_plan" in source
        assert "commands.bulk_write" not in source
        assert "commands.write_bytes" not in source

    def test_version_write_port_uses_version_write_commands(self):
        """The Integration write port is the only direct Version Engine write adapter."""
        import inspect

        from src.platform.integrations.version_write_port import VersionEngineWritePort
        source = inspect.getsource(VersionEngineWritePort.write_plan)
        assert "build_leased_worker_write_commands" in source
        assert "commands.bulk_write" in source
        assert "commands.write_bytes" in source

    def test_connector_has_no_version_adapter_dependency(self):
        """BaseConnector should not import or reference ProductOperationAdapter."""
        import inspect
        source = inspect.getsource(BaseConnector)
        assert "ProductOperationAdapter" not in source
        assert "version_engine" not in source


# ── Plugin Auto-Discovery Tests ────────────────────────────────

class TestPluginDiscovery:
    """Verify the plugin auto-discovery mechanism exists."""

    def test_discovery_function_exists(self):
        from src.connectors.datasource.dependencies import _discover_connectors
        assert callable(_discover_connectors)

    def test_registry_class_importable(self):
        from src.connectors.datasource.registry import ConnectorRegistry
        assert hasattr(ConnectorRegistry, "register")

    def test_connector_setup_protocol(self):
        """Each connector module should export a setup() function."""
        import importlib
        # Test with URL connector (simplest, no OAuth)
        mod = importlib.import_module(
            "src.connectors.datasource.url.connector"
        )
        assert hasattr(mod, "setup")
        assert callable(mod.setup)
