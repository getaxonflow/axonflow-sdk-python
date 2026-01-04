"""Unit tests for AxonFlow client."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, Mode
from axonflow.exceptions import (
    AuthenticationError,
    AxonFlowError,
    PolicyViolationError,
)


class TestClientInitialization:
    """Test client initialization."""

    def test_creates_with_required_params(self, config_dict: dict[str, Any]) -> None:
        """Test client creates with required parameters."""
        client = AxonFlow(**config_dict)
        assert client.config.agent_url == config_dict["agent_url"]
        assert client.config.client_id == config_dict["client_id"]

    def test_default_values_applied(self, config_dict: dict[str, Any]) -> None:
        """Test default configuration values."""
        client = AxonFlow(**config_dict)
        assert client.config.timeout == 60.0
        assert client.config.retry.enabled is True
        assert client.config.retry.max_attempts == 3

    def test_sandbox_mode(self) -> None:
        """Test sandbox client creation."""
        client = AxonFlow.sandbox()
        assert "staging" in client.config.agent_url
        assert client.config.debug is True
        assert client.config.mode == Mode.SANDBOX

    def test_mode_string_conversion(self, config_dict: dict[str, Any]) -> None:
        """Test mode string is converted to enum."""
        client = AxonFlow(**config_dict, mode="sandbox")
        assert client.config.mode == Mode.SANDBOX

    def test_url_trailing_slash_stripped(self) -> None:
        """Test trailing slash is stripped from URL."""
        client = AxonFlow(
            agent_url="https://test.axonflow.com/",
            client_id="test",
            client_secret="test",
        )
        assert client.config.agent_url == "https://test.axonflow.com"

    def test_license_key_optional(self, config_dict: dict[str, Any]) -> None:
        """Test license key is optional."""
        client = AxonFlow(**config_dict)
        assert client.config.license_key is None

        client_with_license = AxonFlow(**config_dict, license_key="license-123")
        assert client_with_license.config.license_key == "license-123"


class TestHealthCheck:
    """Test health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_success(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_health_response: dict[str, Any],
    ) -> None:
        """Test successful health check."""
        httpx_mock.add_response(json=mock_health_response)
        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test health check returns False on error."""
        httpx_mock.add_response(status_code=500)
        result = await client.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test health check returns False when unhealthy."""
        httpx_mock.add_response(json={"status": "unhealthy"})
        result = await client.health_check()
        assert result is False


class TestExecuteQuery:
    """Test query execution."""

    @pytest.mark.asyncio
    async def test_successful_query(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test successful query execution."""
        httpx_mock.add_response(json=mock_query_response)

        result = await client.execute_query(
            user_token="test-token",
            query="What is AI?",
            request_type="chat",
        )

        assert result.success is True
        assert result.blocked is False
        assert result.data == {"result": "test result"}

    @pytest.mark.asyncio
    async def test_blocked_by_policy(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_blocked_response: dict[str, Any],
    ) -> None:
        """Test query blocked by policy raises exception."""
        httpx_mock.add_response(json=mock_blocked_response)

        with pytest.raises(PolicyViolationError) as exc_info:
            await client.execute_query(
                user_token="test-token",
                query="What is AI?",
                request_type="chat",
            )

        assert "Rate limit" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_authentication_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test authentication error handling."""
        httpx_mock.add_response(status_code=401)

        with pytest.raises(AuthenticationError):
            await client.execute_query(
                user_token="bad-token",
                query="test",
                request_type="chat",
            )

    @pytest.mark.asyncio
    async def test_policy_violation_403(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test 403 response raises PolicyViolationError."""
        httpx_mock.add_response(
            status_code=403,
            json={
                "message": "Access denied",
                "policy": "access-control",
                "block_reason": "Insufficient permissions",
            },
        )

        with pytest.raises(PolicyViolationError) as exc_info:
            await client.execute_query(
                user_token="test",
                query="test",
                request_type="chat",
            )

        assert exc_info.value.policy == "access-control"

    @pytest.mark.asyncio
    async def test_generic_http_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test generic HTTP error handling."""
        httpx_mock.add_response(status_code=500, text="Internal Server Error")

        with pytest.raises(AxonFlowError) as exc_info:
            await client.execute_query(
                user_token="test",
                query="test",
                request_type="chat",
            )

        assert "HTTP 500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_with_context(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test query with additional context."""
        httpx_mock.add_response(json=mock_query_response)

        result = await client.execute_query(
            user_token="test-token",
            query="What is AI?",
            request_type="chat",
            context={"session_id": "123", "user_role": "admin"},
        )

        assert result.success is True


class TestCaching:
    """Test response caching."""

    @pytest.mark.asyncio
    async def test_cache_hit(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test cache returns same response."""
        httpx_mock.add_response(json=mock_query_response)

        async with AxonFlow(**config_dict) as client:
            # First call
            result1 = await client.execute_query(
                user_token="test",
                query="cached query",
                request_type="chat",
            )

            # Second call - should hit cache
            result2 = await client.execute_query(
                user_token="test",
                query="cached query",
                request_type="chat",
            )

            assert result1.data == result2.data
            # Only one HTTP request should have been made
            assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_different_query(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test cache miss for different queries."""
        httpx_mock.add_response(json=mock_query_response)
        httpx_mock.add_response(json=mock_query_response)

        async with AxonFlow(**config_dict) as client:
            await client.execute_query("test", "query1", "chat")
            await client.execute_query("test", "query2", "chat")

            # Two HTTP requests should have been made
            assert len(httpx_mock.get_requests()) == 2

    @pytest.mark.asyncio
    async def test_cache_disabled(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test caching can be disabled."""
        httpx_mock.add_response(json=mock_query_response)
        httpx_mock.add_response(json=mock_query_response)

        async with AxonFlow(**config_dict, cache_enabled=False) as client:
            await client.execute_query("test", "query", "chat")
            await client.execute_query("test", "query", "chat")

            # Both requests should be made
            assert len(httpx_mock.get_requests()) == 2


class TestConnectors:
    """Test connector operations."""

    @pytest.mark.asyncio
    async def test_list_connectors(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_connector_list: list[dict[str, Any]],
    ) -> None:
        """Test listing connectors."""
        httpx_mock.add_response(json=mock_connector_list)

        connectors = await client.list_connectors()

        assert len(connectors) == 2
        assert connectors[0].id == "postgres"
        assert connectors[0].installed is True
        assert connectors[1].id == "salesforce"
        assert connectors[1].installed is False

    @pytest.mark.asyncio
    async def test_install_connector(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test installing a connector."""
        from axonflow import ConnectorInstallRequest

        httpx_mock.add_response(status_code=201, json={})

        await client.install_connector(
            ConnectorInstallRequest(
                connector_id="salesforce",
                name="My Salesforce",
                tenant_id="tenant-123",
                options={"api_version": "v55.0"},
                credentials={"api_key": "secret"},
            )
        )

        request = httpx_mock.get_requests()[0]
        assert "/api/v1/connectors/salesforce/install" in str(request.url)

    @pytest.mark.asyncio
    async def test_query_connector(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test querying a connector."""
        httpx_mock.add_response(
            json={
                "success": True,
                "data": {"rows": [{"id": 1}, {"id": 2}]},
            }
        )

        result = await client.query_connector(
            user_token="test",
            connector_name="postgres",
            operation="query",
            params={"sql": "SELECT * FROM users"},
        )

        assert result.success is True
        assert result.data["rows"][0]["id"] == 1


class TestPlanning:
    """Test multi-agent planning."""

    @pytest.mark.asyncio
    async def test_generate_plan(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_plan_response: dict[str, Any],
    ) -> None:
        """Test plan generation."""
        httpx_mock.add_response(json=mock_plan_response)

        plan = await client.generate_plan(
            query="Book a flight and hotel for my trip",
            domain="travel",
        )

        assert plan.plan_id == "plan-123"
        assert len(plan.steps) == 2
        assert plan.steps[0].name == "Fetch data"
        assert plan.steps[1].depends_on == ["step-1"]

    @pytest.mark.asyncio
    async def test_execute_plan(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test plan execution."""
        httpx_mock.add_response(
            json={
                "success": True,
                "result": "Trip booked successfully",
                "metadata": {
                    "duration": "5.2s",
                    "step_results": {"step-1": "done", "step-2": "done"},
                },
            }
        )

        result = await client.execute_plan("plan-123")

        assert result.status == "completed"
        assert result.result == "Trip booked successfully"

    @pytest.mark.asyncio
    async def test_get_plan_status(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting plan status."""
        httpx_mock.add_response(
            json={
                "plan_id": "plan-123",
                "status": "running",
                "step_results": {"step-1": "done"},
            }
        )

        result = await client.get_plan_status("plan-123")

        assert result.status == "running"


class TestSyncClient:
    """Test synchronous client wrapper."""

    def test_sync_health_check(
        self,
        sync_client,
        httpx_mock: HTTPXMock,
        mock_health_response: dict[str, Any],
    ) -> None:
        """Test sync health check."""
        httpx_mock.add_response(json=mock_health_response)
        result = sync_client.health_check()
        assert result is True

    def test_sync_execute_query(
        self,
        sync_client,
        httpx_mock: HTTPXMock,
        mock_query_response: dict[str, Any],
    ) -> None:
        """Test sync query execution."""
        httpx_mock.add_response(json=mock_query_response)
        result = sync_client.execute_query("test", "query", "chat")
        assert result.success is True

    def test_sync_context_manager(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
        mock_health_response: dict[str, Any],
    ) -> None:
        """Test sync context manager."""
        httpx_mock.add_response(json=mock_health_response)

        with AxonFlow.sync(**config_dict) as client:
            result = client.health_check()
            assert result is True


class TestContextManager:
    """Test async context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
        mock_health_response: dict[str, Any],
    ) -> None:
        """Test async context manager."""
        httpx_mock.add_response(json=mock_health_response)

        async with AxonFlow(**config_dict) as client:
            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_explicit_close(
        self,
        config_dict: dict[str, Any],
    ) -> None:
        """Test explicit close."""
        client = AxonFlow(**config_dict)
        await client.close()
        # No exception should be raised


class TestParseDatetime:
    """Test _parse_datetime helper function."""

    def test_parse_utc_z_suffix(self) -> None:
        """Test parsing datetime with Z suffix."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00Z")
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.second == 0

    def test_parse_with_offset(self) -> None:
        """Test parsing datetime with timezone offset."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00+00:00")
        assert result.year == 2024
        assert result.hour == 10

    def test_parse_microseconds(self) -> None:
        """Test parsing datetime with microseconds (6 digits)."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00.123456Z")
        assert result.microsecond == 123456

    def test_parse_nanoseconds_truncated(self) -> None:
        """Test that nanoseconds (9 digits) are truncated to microseconds."""
        from axonflow.client import _parse_datetime

        # This would fail without the fix - 9 fractional digits
        result = _parse_datetime("2024-12-15T10:30:00.123456789Z")
        # Should be truncated to 123456 (first 6 digits)
        assert result.microsecond == 123456

    def test_parse_nanoseconds_with_offset(self) -> None:
        """Test nanoseconds with timezone offset."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00.123456789+00:00")
        assert result.microsecond == 123456

    def test_parse_milliseconds(self) -> None:
        """Test parsing datetime with milliseconds (3 digits)."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00.123Z")
        assert result.microsecond == 123000

    def test_parse_7_fractional_digits(self) -> None:
        """Test parsing datetime with 7 fractional digits."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00.1234567Z")
        # Should truncate to 6 digits
        assert result.microsecond == 123456

    def test_parse_no_fractional_seconds(self) -> None:
        """Test parsing datetime without fractional seconds."""
        from axonflow.client import _parse_datetime

        result = _parse_datetime("2024-12-15T10:30:00Z")
        assert result.microsecond == 0


class TestExecutionReplay:
    """Test Execution Replay API methods."""

    @pytest.mark.asyncio
    async def test_list_executions_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing executions returns empty list."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions",
            json={"executions": [], "total": 0, "limit": 50, "offset": 0},
        )
        result = await client.list_executions()
        assert result.total == 0
        assert result.executions == []
        assert result.limit == 50

    @pytest.mark.asyncio
    async def test_list_executions_with_data(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing executions returns data."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions?status=completed&limit=10",
            json={
                "executions": [
                    {
                        "request_id": "exec-123",
                        "workflow_name": "test-workflow",
                        "status": "completed",
                        "total_steps": 3,
                        "completed_steps": 3,
                        "started_at": "2026-01-03T12:00:00Z",
                        "completed_at": "2026-01-03T12:00:05Z",
                        "duration_ms": 5000,
                        "total_tokens": 150,
                        "total_cost_usd": 0.01,
                    }
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            },
        )
        from axonflow.types import ListExecutionsOptions

        result = await client.list_executions(ListExecutionsOptions(status="completed", limit=10))
        assert result.total == 1
        assert len(result.executions) == 1
        assert result.executions[0].request_id == "exec-123"
        assert result.executions[0].status == "completed"

    @pytest.mark.asyncio
    async def test_get_execution(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting a single execution."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123",
            json={
                "summary": {
                    "request_id": "exec-123",
                    "workflow_name": "test-workflow",
                    "status": "completed",
                    "total_steps": 2,
                    "completed_steps": 2,
                    "started_at": "2026-01-03T12:00:00Z",
                    "completed_at": "2026-01-03T12:00:05Z",
                    "duration_ms": 5000,
                    "total_tokens": 100,
                    "total_cost_usd": 0.005,
                },
                "steps": [
                    {
                        "request_id": "exec-123",
                        "step_index": 0,
                        "step_name": "greet",
                        "status": "completed",
                        "started_at": "2026-01-03T12:00:00Z",
                        "completed_at": "2026-01-03T12:00:02Z",
                        "duration_ms": 2000,
                        "provider": "anthropic",
                        "model": "claude-sonnet-4",
                        "tokens_in": 20,
                        "tokens_out": 30,
                        "cost_usd": 0.002,
                    },
                    {
                        "request_id": "exec-123",
                        "step_index": 1,
                        "step_name": "process",
                        "status": "completed",
                        "started_at": "2026-01-03T12:00:02Z",
                        "completed_at": "2026-01-03T12:00:05Z",
                        "duration_ms": 3000,
                        "provider": "openai",
                        "model": "gpt-4",
                        "tokens_in": 25,
                        "tokens_out": 25,
                        "cost_usd": 0.003,
                    },
                ],
            },
        )
        result = await client.get_execution("exec-123")
        assert result.summary.request_id == "exec-123"
        assert result.summary.status == "completed"
        assert len(result.steps) == 2
        assert result.steps[0].step_name == "greet"
        assert result.steps[1].step_name == "process"

    @pytest.mark.asyncio
    async def test_get_execution_steps(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting execution steps."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123/steps",
            json=[
                {
                    "request_id": "exec-123",
                    "step_index": 0,
                    "step_name": "step1",
                    "status": "completed",
                    "started_at": "2026-01-03T12:00:00Z",
                    "completed_at": "2026-01-03T12:00:01Z",
                    "duration_ms": 1000,
                    "tokens_in": 10,
                    "tokens_out": 15,
                    "cost_usd": 0.001,
                },
                {
                    "request_id": "exec-123",
                    "step_index": 1,
                    "step_name": "step2",
                    "status": "completed",
                    "started_at": "2026-01-03T12:00:01Z",
                    "completed_at": "2026-01-03T12:00:02Z",
                    "duration_ms": 1000,
                    "tokens_in": 15,
                    "tokens_out": 20,
                    "cost_usd": 0.002,
                },
            ],
        )
        result = await client.get_execution_steps("exec-123")
        assert len(result) == 2
        assert result[0].step_index == 0
        assert result[0].step_name == "step1"
        assert result[1].step_index == 1
        assert result[1].step_name == "step2"

    @pytest.mark.asyncio
    async def test_get_execution_timeline(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting execution timeline."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123/timeline",
            json=[
                {
                    "step_index": 0,
                    "step_name": "start",
                    "status": "completed",
                    "started_at": "2026-01-03T12:00:00Z",
                    "completed_at": "2026-01-03T12:00:01Z",
                    "duration_ms": 1000,
                    "has_error": False,
                    "has_approval": False,
                },
                {
                    "step_index": 1,
                    "step_name": "approve",
                    "status": "completed",
                    "started_at": "2026-01-03T12:00:01Z",
                    "completed_at": "2026-01-03T12:00:10Z",
                    "duration_ms": 9000,
                    "has_error": False,
                    "has_approval": True,
                },
                {
                    "step_index": 2,
                    "step_name": "finish",
                    "status": "failed",
                    "started_at": "2026-01-03T12:00:10Z",
                    "completed_at": "2026-01-03T12:00:11Z",
                    "duration_ms": 1000,
                    "has_error": True,
                    "has_approval": False,
                },
            ],
        )
        result = await client.get_execution_timeline("exec-123")
        assert len(result) == 3
        assert result[0].step_name == "start"
        assert result[0].has_error is False
        assert result[1].step_name == "approve"
        assert result[1].has_approval is True
        assert result[2].step_name == "finish"
        assert result[2].has_error is True

    @pytest.mark.asyncio
    async def test_export_execution(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test exporting execution with custom options."""
        # Setting include_policies=False to test non-default behavior
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123/export?format=json&include_input=true&include_output=true",
            json={
                "execution_id": "exec-123",
                "workflow_name": "test-workflow",
                "exported_at": "2026-01-03T12:00:00Z",
                "summary": {"status": "completed"},
                "steps": [{"step_index": 0, "input": {"query": "test"}}],
            },
        )
        from axonflow.types import ExecutionExportOptions

        result = await client.export_execution(
            "exec-123",
            ExecutionExportOptions(include_input=True, include_output=True, include_policies=False),
        )
        assert result["execution_id"] == "exec-123"
        assert result["workflow_name"] == "test-workflow"
        assert "steps" in result

    @pytest.mark.asyncio
    async def test_export_execution_no_options(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test exporting execution without options."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123/export",
            json={
                "execution_id": "exec-123",
                "exported_at": "2026-01-03T12:00:00Z",
            },
        )
        result = await client.export_execution("exec-123")
        assert result["execution_id"] == "exec-123"

    @pytest.mark.asyncio
    async def test_delete_execution(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test deleting execution."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/exec-123",
            method="DELETE",
            status_code=204,
        )
        # Should not raise
        await client.delete_execution("exec-123")

    @pytest.mark.asyncio
    async def test_execution_not_found(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test execution not found returns 404."""
        httpx_mock.add_response(
            url="https://test.axonflow.com:8081/api/v1/executions/nonexistent",
            status_code=404,
            json={"error": "execution not found"},
        )
        with pytest.raises(Exception):  # noqa: B017
            await client.get_execution("nonexistent")
