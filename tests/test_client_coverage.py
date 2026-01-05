"""Additional tests for AxonFlow client to increase coverage."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, Mode, SyncAxonFlow
from axonflow.types import ExecutionExportOptions, ListExecutionsOptions


class TestSyncClientCoverage:
    """Extended tests for SyncAxonFlow client."""

    def test_sync_client_creation(self, config_dict: dict[str, Any]) -> None:
        """Test creating a sync client."""
        client = AxonFlow.sync(**config_dict)
        assert isinstance(client, SyncAxonFlow)
        assert client.config.endpoint == config_dict["endpoint"]
        client.close()

    def test_sync_client_context_manager(self, config_dict: dict[str, Any]) -> None:
        """Test sync client as context manager."""
        with AxonFlow.sync(**config_dict) as client:
            assert isinstance(client, SyncAxonFlow)
            assert client.config.client_id == config_dict["client_id"]

    def test_sync_execute_query_basic(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync execute_query."""
        httpx_mock.add_response(
            json={
                "success": True,
                "blocked": False,
                "data": {"result": "test"},
            }
        )

        result = sync_client.execute_query(
            user_token="test-user",
            query="Test query",
            request_type="chat",
        )
        assert result.success is True
        assert result.blocked is False


class TestExceptionsCoverage:
    """Tests for exception types."""

    def test_all_exception_types(self) -> None:
        """Test all exception types can be created."""
        from axonflow.exceptions import (
            AuthenticationError,
            AxonFlowError,
            ConfigurationError,
            ConnectionError,
            ConnectorError,
            PlanExecutionError,
            PolicyViolationError,
            RateLimitError,
            TimeoutError,
        )

        assert AxonFlowError("test") is not None
        assert ConfigurationError("test") is not None
        assert AuthenticationError("test") is not None
        assert PolicyViolationError("test") is not None
        # RateLimitError requires limit and remaining
        assert RateLimitError("test", limit=100, remaining=0) is not None
        assert ConnectionError("test") is not None
        assert TimeoutError("test") is not None
        assert ConnectorError("test") is not None
        assert PlanExecutionError("test") is not None

    def test_exception_with_details(self) -> None:
        """Test exceptions with details."""
        from axonflow.exceptions import (
            AxonFlowError,
            ConnectorError,
            PlanExecutionError,
            PolicyViolationError,
            RateLimitError,
        )

        err = AxonFlowError("test", details={"key": "value"})
        assert err.details == {"key": "value"}

        policy_err = PolicyViolationError("blocked", policy="pii", block_reason="PII")
        assert policy_err.policy == "pii"
        assert policy_err.block_reason == "PII"

        rate_err = RateLimitError("rate limit", limit=100, remaining=0, reset_at="2025-01-01")
        assert rate_err.limit == 100
        assert rate_err.remaining == 0
        assert rate_err.reset_at == "2025-01-01"

        conn_err = ConnectorError("failed", connector="postgres", operation="query")
        assert conn_err.connector == "postgres"
        assert conn_err.operation == "query"

        plan_err = PlanExecutionError("failed", plan_id="plan-1", step="step-1")
        assert plan_err.plan_id == "plan-1"
        assert plan_err.step == "step-1"


class TestConnectorCoverage:
    """Extended tests for connector methods."""

    @pytest.mark.asyncio
    async def test_get_connector_not_installed(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting a non-installed connector."""
        httpx_mock.add_response(
            json={
                "id": "new-connector",
                "name": "New Connector",
                "type": "database",
                "version": "1.0.0",
                "description": "A new connector",
                "category": "database",
                "tags": [],
                "capabilities": [],
                "installed": False,
                "healthy": False,
            }
        )

        result = await client.get_connector("new-connector")
        assert result.installed is False


class TestModeConversion:
    """Test mode conversion functionality."""

    def test_mode_enum_values(self) -> None:
        """Test Mode enum values."""
        assert Mode.PRODUCTION.value == "production"
        assert Mode.SANDBOX.value == "sandbox"

    def test_mode_from_string_production(self, config_dict: dict[str, Any]) -> None:
        """Test mode string conversion for production."""
        client = AxonFlow(**config_dict, mode="production")
        assert client.config.mode == Mode.PRODUCTION

    def test_mode_enum_direct(self, config_dict: dict[str, Any]) -> None:
        """Test mode enum passed directly."""
        client = AxonFlow(**config_dict, mode=Mode.SANDBOX)
        assert client.config.mode == Mode.SANDBOX


class TestAsyncContextManager:
    """Test async context manager functionality."""

    @pytest.mark.asyncio
    async def test_async_context_manager_health(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test async context manager performs health check."""
        httpx_mock.add_response(json={"status": "healthy"})

        async with AxonFlow(**config_dict) as client:
            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_async_client_explicit_close(
        self,
        config_dict: dict[str, Any],
    ) -> None:
        """Test explicit close of async client."""
        client = AxonFlow(**config_dict)
        await client.close()
        # Verify client can be closed multiple times without error
        await client.close()


class TestEventLoopHandling:
    """Test event loop handling in sync client."""

    def test_sync_client_creates_own_loop(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync client creates its own event loop."""
        httpx_mock.add_response(json={"status": "healthy"})

        with AxonFlow.sync(**config_dict) as client:
            result = client.health_check()
            assert result is True

    def test_sync_client_loop_cleanup(
        self,
        config_dict: dict[str, Any],
    ) -> None:
        """Test sync client cleans up loop on close."""
        client = AxonFlow.sync(**config_dict)
        # Access internal method to verify loop handling
        client._get_loop()
        client.close()


class TestCacheConfigCoverage:
    """Tests for cache configuration."""

    def test_cache_config_default(self) -> None:
        """Test cache config with defaults."""
        from axonflow.types import CacheConfig

        config = CacheConfig()
        assert config.enabled is True
        assert config.ttl == 60.0
        assert config.max_size == 1000

    def test_cache_config_custom(self) -> None:
        """Test cache config with custom values."""
        from axonflow.types import CacheConfig

        config = CacheConfig(enabled=False, ttl=120.0, max_size=500)
        assert config.enabled is False
        assert config.ttl == 120.0
        assert config.max_size == 500


class TestRetryConfigCoverage:
    """Tests for retry configuration."""

    def test_retry_config_default(self) -> None:
        """Test retry config with defaults."""
        from axonflow.types import RetryConfig

        config = RetryConfig()
        assert config.enabled is True
        assert config.max_attempts == 3

    def test_retry_config_disabled(self) -> None:
        """Test retry config when disabled."""
        from axonflow.types import RetryConfig

        config = RetryConfig(enabled=False)
        assert config.enabled is False
