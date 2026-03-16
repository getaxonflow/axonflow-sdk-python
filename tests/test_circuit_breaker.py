"""Tests for circuit breaker observability methods."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError
from axonflow.types import (
    CircuitBreakerConfig,
    CircuitBreakerConfigUpdate,
    CircuitBreakerHistoryEntry,
    CircuitBreakerHistoryResponse,
    CircuitBreakerStatusResponse,
)


class TestGetCircuitBreakerStatus:
    """Tests for get_circuit_breaker_status method."""

    @pytest.mark.asyncio
    async def test_success_with_active_circuits(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful status response with active circuits."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "active_circuits": [
                        {
                            "id": "cb_001",
                            "scope": "tenant",
                            "scope_id": "tenant-abc",
                            "state": "open",
                            "trip_reason": "error_threshold_exceeded",
                        },
                    ],
                    "count": 1,
                    "emergency_stop_active": True,
                },
            },
        )

        result = await client.get_circuit_breaker_status()

        assert isinstance(result, CircuitBreakerStatusResponse)
        assert result.count == 1
        assert result.emergency_stop_active is True
        assert len(result.active_circuits) == 1
        assert result.active_circuits[0]["id"] == "cb_001"

    @pytest.mark.asyncio
    async def test_success_no_active_circuits(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test status response when no circuits are active."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "active_circuits": [],
                    "count": 0,
                    "emergency_stop_active": False,
                },
            },
        )

        result = await client.get_circuit_breaker_status()

        assert result.count == 0
        assert result.emergency_stop_active is False
        assert result.active_circuits == []

    @pytest.mark.asyncio
    async def test_success_without_data_wrapper(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test status response without data wrapper (flat response)."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "active_circuits": [],
                "count": 0,
                "emergency_stop_active": False,
            },
        )

        result = await client.get_circuit_breaker_status()

        assert result.count == 0
        assert result.emergency_stop_active is False

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_circuit_breaker_status()


class TestGetCircuitBreakerHistory:
    """Tests for get_circuit_breaker_history method."""

    @pytest.mark.asyncio
    async def test_success_with_entries(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful history response with entries."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "history": [
                        {
                            "id": "cb_001",
                            "org_id": "org-123",
                            "scope": "tenant",
                            "scope_id": "tenant-abc",
                            "state": "open",
                            "trip_reason": "error_threshold_exceeded",
                            "tripped_by": "auto",
                            "tripped_at": "2026-03-16T10:00:00Z",
                            "expires_at": "2026-03-16T10:05:00Z",
                            "error_count": 15,
                            "violation_count": 3,
                        },
                        {
                            "id": "cb_002",
                            "org_id": "org-123",
                            "scope": "global",
                            "state": "closed",
                            "reset_by": "admin@example.com",
                            "reset_at": "2026-03-16T09:30:00Z",
                            "error_count": 0,
                            "violation_count": 0,
                        },
                    ],
                    "count": 2,
                },
            },
        )

        result = await client.get_circuit_breaker_history()

        assert isinstance(result, CircuitBreakerHistoryResponse)
        assert result.count == 2
        assert len(result.history) == 2

        entry = result.history[0]
        assert isinstance(entry, CircuitBreakerHistoryEntry)
        assert entry.id == "cb_001"
        assert entry.scope == "tenant"
        assert entry.scope_id == "tenant-abc"
        assert entry.state == "open"
        assert entry.trip_reason == "error_threshold_exceeded"
        assert entry.error_count == 15
        assert entry.violation_count == 3

        entry2 = result.history[1]
        assert entry2.state == "closed"
        assert entry2.reset_by == "admin@example.com"
        assert entry2.scope_id == ""

    @pytest.mark.asyncio
    async def test_with_limit_parameter(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that limit query parameter is sent correctly."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "history": [],
                    "count": 0,
                },
            },
        )

        await client.get_circuit_breaker_history(limit=10)

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        assert "limit=10" in str(sent_request.url)

    @pytest.mark.asyncio
    async def test_without_limit_parameter(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that no limit query parameter is sent when not specified."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "history": [],
                    "count": 0,
                },
            },
        )

        await client.get_circuit_breaker_history()

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        assert "limit" not in str(sent_request.url)

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_circuit_breaker_history()


class TestGetCircuitBreakerConfig:
    """Tests for get_circuit_breaker_config method."""

    @pytest.mark.asyncio
    async def test_global_config(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting global circuit breaker config."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "source": "global",
                    "error_threshold": 10,
                    "violation_threshold": 5,
                    "window_seconds": 60,
                    "default_timeout_seconds": 300,
                    "max_timeout_seconds": 3600,
                    "enable_auto_recovery": True,
                },
            },
        )

        result = await client.get_circuit_breaker_config()

        assert isinstance(result, CircuitBreakerConfig)
        assert result.source == "global"
        assert result.error_threshold == 10
        assert result.violation_threshold == 5
        assert result.window_seconds == 60
        assert result.default_timeout_seconds == 300
        assert result.max_timeout_seconds == 3600
        assert result.enable_auto_recovery is True
        assert result.tenant_id is None
        assert result.overrides is None

    @pytest.mark.asyncio
    async def test_tenant_config(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting tenant-specific circuit breaker config."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "source": "tenant",
                    "error_threshold": 20,
                    "violation_threshold": 10,
                    "window_seconds": 120,
                    "default_timeout_seconds": 600,
                    "max_timeout_seconds": 7200,
                    "enable_auto_recovery": False,
                    "tenant_id": "tenant-123",
                    "overrides": {"error_threshold": 20, "violation_threshold": 10},
                },
            },
        )

        result = await client.get_circuit_breaker_config(tenant_id="tenant-123")

        assert result.source == "tenant"
        assert result.error_threshold == 20
        assert result.tenant_id == "tenant-123"
        assert result.overrides is not None
        assert result.overrides["error_threshold"] == 20

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        assert "tenant_id=tenant-123" in str(sent_request.url)

    @pytest.mark.asyncio
    async def test_without_tenant_id(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that no tenant_id query parameter is sent when not specified."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "source": "global",
                    "error_threshold": 10,
                    "violation_threshold": 5,
                    "window_seconds": 60,
                    "default_timeout_seconds": 300,
                    "max_timeout_seconds": 3600,
                    "enable_auto_recovery": True,
                },
            },
        )

        await client.get_circuit_breaker_config()

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        assert "tenant_id" not in str(sent_request.url)

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_circuit_breaker_config()


class TestUpdateCircuitBreakerConfig:
    """Tests for update_circuit_breaker_config method."""

    @pytest.mark.asyncio
    async def test_success(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful config update."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "status": "updated",
                    "tenant_id": "tenant-123",
                },
            },
        )

        config = CircuitBreakerConfigUpdate(
            tenant_id="tenant-123",
            error_threshold=20,
            violation_threshold=10,
        )

        result = await client.update_circuit_breaker_config(config)

        assert result["status"] == "updated"
        assert result["tenant_id"] == "tenant-123"

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        body = json.loads(sent_request.content)
        assert body["tenant_id"] == "tenant-123"
        assert body["error_threshold"] == 20
        assert body["violation_threshold"] == 10
        # None fields should be excluded
        assert "window_seconds" not in body
        assert "default_timeout_seconds" not in body

    @pytest.mark.asyncio
    async def test_empty_tenant_id_raises(
        self,
        client: AxonFlow,
    ) -> None:
        """Test that empty tenant_id raises ValueError."""
        config = CircuitBreakerConfigUpdate(tenant_id="")

        with pytest.raises(ValueError, match="tenant_id is required"):
            await client.update_circuit_breaker_config(config)

    @pytest.mark.asyncio
    async def test_whitespace_tenant_id_raises(
        self,
        client: AxonFlow,
    ) -> None:
        """Test that whitespace-only tenant_id raises ValueError."""
        config = CircuitBreakerConfigUpdate(tenant_id="   ")

        with pytest.raises(ValueError, match="tenant_id is required"):
            await client.update_circuit_breaker_config(config)

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        config = CircuitBreakerConfigUpdate(
            tenant_id="tenant-123",
            error_threshold=10,
        )

        with pytest.raises(AxonFlowError):
            await client.update_circuit_breaker_config(config)

    @pytest.mark.asyncio
    async def test_400_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that 400 errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=400,
            json={"error": "invalid configuration"},
        )

        config = CircuitBreakerConfigUpdate(
            tenant_id="tenant-123",
            error_threshold=-1,
        )

        with pytest.raises(AxonFlowError):
            await client.update_circuit_breaker_config(config)


class TestCircuitBreakerTypes:
    """Tests for circuit breaker Pydantic model validation."""

    def test_status_response_model_validate(self) -> None:
        """Test CircuitBreakerStatusResponse model validation."""
        response = CircuitBreakerStatusResponse.model_validate(
            {
                "active_circuits": [{"id": "cb_001", "state": "open"}],
                "count": 1,
                "emergency_stop_active": True,
            }
        )

        assert response.count == 1
        assert response.emergency_stop_active is True
        assert len(response.active_circuits) == 1

    def test_status_response_defaults(self) -> None:
        """Test CircuitBreakerStatusResponse defaults."""
        response = CircuitBreakerStatusResponse(
            count=0,
            emergency_stop_active=False,
        )

        assert response.active_circuits == []

    def test_history_entry_model_validate(self) -> None:
        """Test CircuitBreakerHistoryEntry model validation."""
        entry = CircuitBreakerHistoryEntry.model_validate(
            {
                "id": "cb_001",
                "org_id": "org-123",
                "scope": "tenant",
                "scope_id": "tenant-abc",
                "state": "open",
                "trip_reason": "error_threshold_exceeded",
                "error_count": 15,
                "violation_count": 3,
            }
        )

        assert entry.id == "cb_001"
        assert entry.scope == "tenant"
        assert entry.trip_reason == "error_threshold_exceeded"
        assert entry.tripped_by is None
        assert entry.reset_by is None

    def test_history_entry_defaults(self) -> None:
        """Test CircuitBreakerHistoryEntry defaults."""
        entry = CircuitBreakerHistoryEntry(
            id="cb_001",
            org_id="org-123",
            scope="global",
            state="closed",
        )

        assert entry.scope_id == ""
        assert entry.trip_reason is None
        assert entry.error_count == 0
        assert entry.violation_count == 0

    def test_config_model_validate(self) -> None:
        """Test CircuitBreakerConfig model validation."""
        config = CircuitBreakerConfig.model_validate(
            {
                "source": "global",
                "error_threshold": 10,
                "violation_threshold": 5,
                "window_seconds": 60,
                "default_timeout_seconds": 300,
                "max_timeout_seconds": 3600,
                "enable_auto_recovery": True,
            }
        )

        assert config.source == "global"
        assert config.error_threshold == 10
        assert config.tenant_id is None

    def test_config_update_serialization_excludes_none(self) -> None:
        """Test that model_dump excludes None fields."""
        config = CircuitBreakerConfigUpdate(
            tenant_id="tenant-123",
            error_threshold=20,
        )
        data = config.model_dump(by_alias=True, exclude_none=True)

        assert data == {"tenant_id": "tenant-123", "error_threshold": 20}
        assert "violation_threshold" not in data
        assert "window_seconds" not in data
