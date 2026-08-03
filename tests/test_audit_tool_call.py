"""Tests for audit_tool_call method."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import AuthenticationError, AxonFlowError
from axonflow.types import AuditToolCallRequest, AuditToolCallResponse


class TestAuditToolCall:
    """Tests for audit_tool_call method."""

    @pytest.mark.asyncio
    async def test_success_with_all_fields(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful audit with all fields populated."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_abc123",
                "status": "recorded",
                "timestamp": "2026-03-14T10:30:00Z",
            },
        )

        request = AuditToolCallRequest(
            tool_name="getUserInfo",
            tool_type="mcp",
            input={"user_id": "u123"},
            output={"name": "Alice", "email": "alice@example.com"},
            workflow_id="wf_abc123",
            step_id="step-3",
            user_id="user@example.com",
            duration_ms=45,
            policies_applied=["pii"],
            success=True,
            error_message="",
        )

        result = await client.audit_tool_call(request)

        assert isinstance(result, AuditToolCallResponse)
        assert result.audit_id == "aud_abc123"
        assert result.status == "recorded"
        assert result.timestamp == "2026-03-14T10:30:00Z"

    @pytest.mark.asyncio
    async def test_success_required_only(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful audit with only required fields."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_minimal",
                "status": "recorded",
                "timestamp": "2026-03-14T11:00:00Z",
            },
        )

        request = AuditToolCallRequest(tool_name="simpleCheck")

        result = await client.audit_tool_call(request)

        assert result.audit_id == "aud_minimal"
        assert result.status == "recorded"

    @pytest.mark.asyncio
    async def test_empty_tool_name_raises(
        self,
        client: AxonFlow,
    ) -> None:
        """Test that empty tool_name raises ValueError."""
        request = AuditToolCallRequest(tool_name="")

        with pytest.raises(ValueError, match="tool_name is required"):
            await client.audit_tool_call(request)

    @pytest.mark.asyncio
    async def test_whitespace_tool_name_raises(
        self,
        client: AxonFlow,
    ) -> None:
        """Test that whitespace-only tool_name raises ValueError."""
        request = AuditToolCallRequest(tool_name="   ")

        with pytest.raises(ValueError, match="tool_name is required"):
            await client.audit_tool_call(request)

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

        request = AuditToolCallRequest(tool_name="getUserInfo")

        with pytest.raises(AxonFlowError):
            await client.audit_tool_call(request)

    @pytest.mark.asyncio
    async def test_400_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that 400 errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=400,
            json={"error": "invalid request"},
        )

        request = AuditToolCallRequest(tool_name="getUserInfo")

        with pytest.raises(AxonFlowError):
            await client.audit_tool_call(request)

    @pytest.mark.asyncio
    async def test_401_not_retried_issue_2275(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        # Regression test for getaxonflow/axonflow-enterprise#2275: 401 on
        # /api/v1/audit/tool-call must be terminal — the SDK must NOT retry
        # an auth failure, because retrying with the same invalid token just
        # compounds the storm on the agent.
        #
        # Python SDK is already safe: `_request_with_retry` only retries on
        # `httpx.ConnectError` / `httpx.TimeoutException`, never on HTTP
        # status codes. This test locks in the contract.
        httpx_mock.add_response(
            status_code=401,
            json={"error": "unauthorized"},
        )

        request = AuditToolCallRequest(tool_name="getUserInfo")

        with pytest.raises(AuthenticationError):
            await client.audit_tool_call(request)

        # Exactly one outbound request — no retry. If `_request_with_retry`
        # ever gains a status-code-based retry that includes 401, pytest-httpx
        # will see additional requests against the single registered response
        # and fail.
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_caller_name_sent_in_request_body(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that caller_name is sent in the outgoing request body when supplied."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_caller",
                "status": "recorded",
                "timestamp": "2026-03-14T13:00:00Z",
            },
        )

        request = AuditToolCallRequest(
            tool_name="getUserInfo",
            caller_name="claude_code",
            success=True,
        )

        result = await client.audit_tool_call(request)

        assert result.audit_id == "aud_caller"

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        import json

        body = json.loads(sent_request.content)
        assert body["caller_name"] == "claude_code"
        assert "tool_type" not in body

    @pytest.mark.asyncio
    async def test_tool_type_standalone_backward_compat(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that tool_type alone still works without caller_name (backward compat)."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_legacy",
                "status": "recorded",
                "timestamp": "2026-03-14T13:15:00Z",
            },
        )

        request = AuditToolCallRequest(
            tool_name="getUserInfo",
            tool_type="mcp",
            success=True,
        )

        result = await client.audit_tool_call(request)

        assert result.audit_id == "aud_legacy"

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        import json

        body = json.loads(sent_request.content)
        assert body["tool_type"] == "mcp"
        assert "caller_name" not in body

    @pytest.mark.asyncio
    async def test_caller_name_and_tool_type_together(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that caller_name and tool_type can both be supplied together."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_both",
                "status": "recorded",
                "timestamp": "2026-03-14T13:30:00Z",
            },
        )

        request = AuditToolCallRequest(
            tool_name="getUserInfo",
            caller_name="codex",
            tool_type="mcp",
            success=True,
        )

        result = await client.audit_tool_call(request)

        assert result.audit_id == "aud_both"

        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        import json

        body = json.loads(sent_request.content)
        assert body["caller_name"] == "codex"
        assert body["tool_type"] == "mcp"

    @pytest.mark.asyncio
    async def test_excludes_none_fields_from_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that None optional fields are excluded from the request body."""
        httpx_mock.add_response(
            status_code=201,
            json={
                "audit_id": "aud_sparse",
                "status": "recorded",
                "timestamp": "2026-03-14T12:00:00Z",
            },
        )

        request = AuditToolCallRequest(
            tool_name="checkAccess",
            tool_type="api",
            success=True,
        )

        result = await client.audit_tool_call(request)

        assert result.audit_id == "aud_sparse"

        # Verify the request sent to the server excludes None fields
        sent_request = httpx_mock.get_request()
        assert sent_request is not None
        import json

        body = json.loads(sent_request.content)
        assert "tool_name" in body
        assert "tool_type" in body
        assert "success" in body
        # None fields should not be present
        assert "input" not in body
        assert "output" not in body
        assert "workflow_id" not in body
        assert "step_id" not in body
        assert "user_id" not in body
        assert "duration_ms" not in body
        assert "policies_applied" not in body
        assert "error_message" not in body


class TestAuditToolCallTypes:
    """Tests for AuditToolCallRequest and AuditToolCallResponse types."""

    def test_request_model_validate(self) -> None:
        """Test AuditToolCallRequest model validation."""
        request = AuditToolCallRequest.model_validate(
            {
                "tool_name": "myTool",
                "tool_type": "mcp",
                "input": {"key": "value"},
                "duration_ms": 100,
            }
        )

        assert request.tool_name == "myTool"
        assert request.tool_type == "mcp"
        assert request.input == {"key": "value"}
        assert request.duration_ms == 100
        assert request.output is None
        assert request.workflow_id is None

    def test_response_model_validate(self) -> None:
        """Test AuditToolCallResponse model validation."""
        response = AuditToolCallResponse.model_validate(
            {
                "audit_id": "aud_123",
                "status": "recorded",
                "timestamp": "2026-03-14T10:00:00Z",
            }
        )

        assert response.audit_id == "aud_123"
        assert response.status == "recorded"
        assert response.timestamp == "2026-03-14T10:00:00Z"

    def test_request_model_validate_with_caller_name(self) -> None:
        """Test AuditToolCallRequest model validation with caller_name."""
        request = AuditToolCallRequest.model_validate(
            {
                "tool_name": "myTool",
                "caller_name": "cursor",
                "input": {"key": "value"},
                "duration_ms": 100,
            }
        )

        assert request.tool_name == "myTool"
        assert request.caller_name == "cursor"
        assert request.tool_type is None

    def test_request_serialization_excludes_none(self) -> None:
        """Test that model_dump excludes None fields."""
        request = AuditToolCallRequest(tool_name="myTool")
        data = request.model_dump(by_alias=True, exclude_none=True)

        assert data == {"tool_name": "myTool"}

    def test_request_serialization_includes_all(self) -> None:
        """Test full serialization with all fields."""
        request = AuditToolCallRequest(
            tool_name="myTool",
            tool_type="mcp",
            input={"a": 1},
            output={"b": 2},
            workflow_id="wf_1",
            step_id="s_1",
            user_id="u_1",
            duration_ms=50,
            policies_applied=["pii", "gdpr"],
            success=False,
            error_message="timeout",
        )
        data = request.model_dump(by_alias=True, exclude_none=True)

        assert data["tool_name"] == "myTool"
        assert data["tool_type"] == "mcp"
        assert data["input"] == {"a": 1}
        assert data["output"] == {"b": 2}
        assert data["workflow_id"] == "wf_1"
        assert data["policies_applied"] == ["pii", "gdpr"]
        assert data["success"] is False
        assert data["error_message"] == "timeout"
