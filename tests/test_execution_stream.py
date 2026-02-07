"""Tests for StreamExecutionStatus SSE client method."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.execution import (
    ExecutionStatus,
    ExecutionStatusValue,
    ExecutionType,
)


def _make_execution_event(
    execution_id: str = "exec_stream_1",
    execution_type: str = "map_plan",
    name: str = "test-plan",
    status: str = "running",
    progress_percent: float = 50.0,
    total_steps: int = 3,
    error: str | None = None,
) -> dict[str, Any]:
    """Helper to create an execution status event dict."""
    event = {
        "execution_id": execution_id,
        "execution_type": execution_type,
        "name": name,
        "status": status,
        "current_step_index": 0,
        "total_steps": total_steps,
        "progress_percent": progress_percent,
        "started_at": "2026-02-07T10:00:00Z",
        "created_at": "2026-02-07T10:00:00Z",
        "updated_at": "2026-02-07T10:00:00Z",
    }
    if error:
        event["error"] = error
    return event


def _build_sse_response(*events: dict[str, Any]) -> str:
    """Build an SSE response string from a list of event dicts."""
    lines = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}\n\n")
    return "".join(lines)


class TestStreamExecutionStatus:
    """Test streaming execution status via SSE."""

    @pytest.mark.asyncio
    async def test_stream_basic(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test basic SSE streaming with multiple events ending in terminal status."""
        events = [
            _make_execution_event(status="running", progress_percent=33.3),
            _make_execution_event(status="running", progress_percent=66.7),
            _make_execution_event(status="completed", progress_percent=100.0),
        ]

        sse_body = _build_sse_response(*events)
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 3
        assert received[0].progress_percent == 33.3
        assert received[0].status == ExecutionStatusValue.RUNNING
        assert received[1].progress_percent == 66.7
        assert received[2].status == ExecutionStatusValue.COMPLETED
        assert received[2].progress_percent == 100.0

    @pytest.mark.asyncio
    async def test_stream_empty_id_raises(self, client: AxonFlow) -> None:
        """Test that empty execution ID raises ValueError."""
        with pytest.raises(ValueError, match="Execution ID is required"):
            async for _ in client.stream_execution_status(""):
                pass

    @pytest.mark.asyncio
    async def test_stream_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server error raises AxonFlowError."""
        from axonflow.exceptions import AxonFlowError

        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/nonexistent/stream",
            status_code=404,
            content=b'{"error": "Execution not found"}',
        )

        with pytest.raises(AxonFlowError, match="SSE stream connection failed"):
            async for _ in client.stream_execution_status("nonexistent"):
                pass

    @pytest.mark.asyncio
    async def test_stream_sse_comments_ignored(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that SSE comments and empty lines are properly ignored."""
        event = _make_execution_event(status="completed", progress_percent=100.0)

        # Include comments and empty lines in the SSE stream
        sse_body = (
            ": keep-alive\n\n"
            "\n\n"
            f"data: {json.dumps(event)}\n\n"
        )

        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 1
        assert received[0].status == ExecutionStatusValue.COMPLETED

    @pytest.mark.asyncio
    async def test_stream_invalid_json_skipped(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that invalid JSON events are skipped without error."""
        valid_event = _make_execution_event(status="completed", progress_percent=100.0)

        sse_body = (
            "data: {invalid json}\n\n"
            f"data: {json.dumps(valid_event)}\n\n"
        )

        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        # Only the valid event should be received
        assert len(received) == 1
        assert received[0].status == ExecutionStatusValue.COMPLETED

    @pytest.mark.asyncio
    async def test_stream_failed_terminal(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test streaming ends when failed status is received."""
        events = [
            _make_execution_event(status="running", progress_percent=50.0),
            _make_execution_event(
                status="failed",
                progress_percent=50.0,
                error="Step 2 failed: LLM timeout",
            ),
        ]

        sse_body = _build_sse_response(*events)
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 2
        assert received[0].status == ExecutionStatusValue.RUNNING
        assert received[1].status == ExecutionStatusValue.FAILED
        assert received[1].error == "Step 2 failed: LLM timeout"

    @pytest.mark.asyncio
    async def test_stream_cancelled_terminal(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test streaming ends when cancelled status is received."""
        events = [
            _make_execution_event(status="running", progress_percent=25.0),
            _make_execution_event(status="cancelled", progress_percent=25.0),
        ]

        sse_body = _build_sse_response(*events)
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 2
        assert received[1].status == ExecutionStatusValue.CANCELLED

    @pytest.mark.asyncio
    async def test_stream_with_steps(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test streaming includes step details."""
        event = _make_execution_event(
            execution_type="wcp_workflow",
            status="completed",
            progress_percent=100.0,
            total_steps=2,
        )
        event["steps"] = [
            {
                "step_id": "step-1",
                "step_index": 0,
                "step_name": "LLM Call",
                "step_type": "llm_call",
                "status": "completed",
                "model": "gpt-4",
                "provider": "openai",
                "cost_usd": 0.05,
            },
            {
                "step_id": "step-2",
                "step_index": 1,
                "step_name": "Gate Check",
                "step_type": "gate",
                "status": "completed",
                "decision": "allow",
            },
        ]

        sse_body = _build_sse_response(event)
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 1
        status = received[0]
        assert status.execution_type == ExecutionType.WCP_WORKFLOW
        assert len(status.steps) == 2
        assert status.steps[0].step_name == "LLM Call"
        assert status.steps[0].model == "gpt-4"
        assert status.steps[0].cost_usd == 0.05
        assert status.steps[1].step_name == "Gate Check"
        assert status.steps[1].decision.value == "allow"

    @pytest.mark.asyncio
    async def test_stream_map_plan_type(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test streaming MAP plan execution."""
        event = _make_execution_event(
            execution_type="map_plan",
            status="completed",
            progress_percent=100.0,
        )

        sse_body = _build_sse_response(event)
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 1
        assert received[0].execution_type == ExecutionType.MAP_PLAN
        assert received[0].is_map_plan() is True
        assert received[0].is_wcp_workflow() is False

    @pytest.mark.asyncio
    async def test_stream_data_prefix_no_space(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test SSE parsing with 'data:' prefix without space."""
        event = _make_execution_event(status="completed", progress_percent=100.0)

        # Use "data:" without space (also valid SSE)
        sse_body = f"data:{json.dumps(event)}\n\n"

        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/executions/exec_stream_1/stream",
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

        received: list[ExecutionStatus] = []
        async for status in client.stream_execution_status("exec_stream_1"):
            received.append(status)

        assert len(received) == 1
        assert received[0].status == ExecutionStatusValue.COMPLETED
