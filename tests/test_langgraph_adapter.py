"""Unit tests for AxonFlowLangGraphAdapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, TextContent

from axonflow import AxonFlow
from axonflow.adapters.langgraph import (
    AxonFlowLangGraphAdapter,
    MCPInterceptorOptions,
    WorkflowApprovalRequiredError,
    WorkflowBlockedError,
)
from axonflow.exceptions import PolicyViolationError
from axonflow.types import MCPCheckInputResponse, MCPCheckOutputResponse  # noqa: TC001
from axonflow.workflow import (
    CreateWorkflowResponse,
    GateDecision,
    MarkStepCompletedRequest,
    StepGateRequest,
    StepGateResponse,
    StepType,
    ToolContext,
    WorkflowSource,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    server_name: str = "myserver", name: str = "mytool", args: dict | None = None
) -> Any:
    req = MagicMock()
    req.server_name = server_name
    req.name = name
    req.args = args or {"key": "value"}
    return req


def _input_allowed() -> MCPCheckInputResponse:
    return MCPCheckInputResponse(allowed=True, policies_evaluated=1)


def _input_blocked(reason: str = "Blocked by policy") -> MCPCheckInputResponse:
    return MCPCheckInputResponse(allowed=False, block_reason=reason, policies_evaluated=1)


def _output_allowed(redacted_data: Any = None) -> MCPCheckOutputResponse:
    return MCPCheckOutputResponse(allowed=True, policies_evaluated=1, redacted_data=redacted_data)


def _output_blocked(reason: str = "Output blocked") -> MCPCheckOutputResponse:
    return MCPCheckOutputResponse(allowed=False, block_reason=reason, policies_evaluated=1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPToolInterceptor:
    @pytest.fixture
    def client(self) -> AxonFlow:
        c = MagicMock(spec=AxonFlow)
        c.mcp_check_input = AsyncMock(return_value=_input_allowed())
        c.mcp_check_output = AsyncMock(return_value=_output_allowed())
        return c

    @pytest.fixture
    def adapter(self, client: AxonFlow) -> AxonFlowLangGraphAdapter:
        return AxonFlowLangGraphAdapter(client, "test-workflow")

    # --- happy path ---

    @pytest.mark.asyncio
    async def test_allowed_call_returns_handler_result(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="tool-result")
        request = _make_request()

        result = await adapter.mcp_tool_interceptor()(request, handler)

        assert result == "tool-result"
        handler.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_connector_type_derived_from_request(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        request = _make_request(server_name="srv", name="tool")

        await adapter.mcp_tool_interceptor()(request, handler)

        client.mcp_check_input.assert_awaited_once()
        call_kwargs = client.mcp_check_input.call_args.kwargs
        assert call_kwargs["connector_type"] == "srv.tool"
        assert call_kwargs["parameters"] == request.args
        # Statement uses JSON serialization, not Python repr
        expected_args = json.dumps(request.args, default=str)
        assert call_kwargs["statement"] == f"srv.tool({expected_args})"

    @pytest.mark.asyncio
    async def test_same_connector_type_sent_to_check_output(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        request = _make_request(server_name="srv", name="tool")

        await adapter.mcp_tool_interceptor()(request, handler)

        call_kwargs = client.mcp_check_output.call_args.kwargs
        assert call_kwargs["connector_type"] == "srv.tool"

    @pytest.mark.asyncio
    async def test_output_message_uses_json_serialization(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        result_data = {"rows": [{"id": 1, "name": "test"}]}
        handler = AsyncMock(return_value=result_data)

        await adapter.mcp_tool_interceptor()(_make_request(), handler)

        call_kwargs = client.mcp_check_output.call_args.kwargs
        assert call_kwargs["message"] == json.dumps(result_data, default=str)

    # --- operation ---

    @pytest.mark.asyncio
    async def test_default_operation_is_execute(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.mcp_tool_interceptor()(_make_request(), AsyncMock(return_value="ok"))

        assert client.mcp_check_input.call_args.kwargs["operation"] == "execute"

    @pytest.mark.asyncio
    async def test_custom_operation_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        opts = MCPInterceptorOptions(operation="query")
        await adapter.mcp_tool_interceptor(opts)(_make_request(), AsyncMock(return_value="ok"))

        assert client.mcp_check_input.call_args.kwargs["operation"] == "query"

    # --- input blocked ---

    @pytest.mark.asyncio
    async def test_raises_on_blocked_input(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_input.return_value = _input_blocked("SQL injection detected")
        handler = AsyncMock()

        with pytest.raises(PolicyViolationError, match="SQL injection detected"):
            await adapter.mcp_tool_interceptor()(MagicMock(), handler)

        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocked_input_uses_fallback_message_when_no_reason(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_input.return_value = MCPCheckInputResponse(
            allowed=False, block_reason=None, policies_evaluated=1
        )
        with pytest.raises(PolicyViolationError, match="Tool call blocked by policy"):
            await adapter.mcp_tool_interceptor()(MagicMock(), AsyncMock())

    # --- output blocked ---

    @pytest.mark.asyncio
    async def test_raises_on_blocked_output(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_output.return_value = _output_blocked("Exfiltration limit exceeded")
        handler = AsyncMock(return_value="data")

        with pytest.raises(PolicyViolationError, match="Exfiltration limit exceeded"):
            await adapter.mcp_tool_interceptor()(MagicMock(), handler)

    @pytest.mark.asyncio
    async def test_blocked_output_uses_fallback_message_when_no_reason(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_output.return_value = MCPCheckOutputResponse(
            allowed=False, block_reason=None, policies_evaluated=1
        )
        with pytest.raises(PolicyViolationError, match="Tool result blocked by policy"):
            await adapter.mcp_tool_interceptor()(MagicMock(), AsyncMock(return_value="x"))

    # --- redacted output ---

    @pytest.mark.asyncio
    async def test_returns_redacted_data_when_present(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_output.return_value = _output_allowed(redacted_data="[REDACTED]")
        handler = AsyncMock(return_value="sensitive-data")

        result = await adapter.mcp_tool_interceptor()(MagicMock(), handler)

        assert isinstance(result, CallToolResult)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_returns_original_result_when_no_redaction(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.mcp_check_output.return_value = _output_allowed(redacted_data=None)
        handler = AsyncMock(return_value="clean-data")

        result = await adapter.mcp_tool_interceptor()(MagicMock(), handler)

        assert result == "clean-data"

    # --- custom connector_type_fn ---

    @pytest.mark.asyncio
    async def test_custom_connector_type_fn(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        request = _make_request(server_name="srv", name="tool")
        opts = MCPInterceptorOptions(connector_type_fn=lambda req: req.server_name)

        await adapter.mcp_tool_interceptor(opts)(request, handler)

        call_kwargs = client.mcp_check_input.call_args.kwargs
        assert call_kwargs["connector_type"] == "srv"

    @pytest.mark.asyncio
    async def test_custom_connector_type_fn_also_used_for_output_check(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        request = _make_request(server_name="srv", name="tool")
        opts = MCPInterceptorOptions(connector_type_fn=lambda req: "custom-type")

        await adapter.mcp_tool_interceptor(opts)(request, handler)

        assert client.mcp_check_output.call_args.kwargs["connector_type"] == "custom-type"

    # --- factory returns independent callables ---

    @pytest.mark.asyncio
    async def test_each_call_to_factory_returns_independent_interceptor(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        interceptor_a = adapter.mcp_tool_interceptor()
        interceptor_b = adapter.mcp_tool_interceptor(
            MCPInterceptorOptions(connector_type_fn=lambda r: "other")
        )
        assert interceptor_a is not interceptor_b

        await interceptor_a(_make_request(), handler)
        call_a = client.mcp_check_input.call_args_list[-1].kwargs["connector_type"]

        await interceptor_b(_make_request(), handler)
        call_b = client.mcp_check_input.call_args_list[-1].kwargs["connector_type"]

        assert call_a != call_b


# ---------------------------------------------------------------------------
# Shared helpers for workflow gate tests
# ---------------------------------------------------------------------------


def _make_allow_response(step_id: str = "step-1-test") -> StepGateResponse:
    return StepGateResponse(
        decision=GateDecision.ALLOW,
        step_id=step_id,
    )


def _make_block_response(step_id: str = "step-1-test") -> StepGateResponse:
    return StepGateResponse(
        decision=GateDecision.BLOCK,
        step_id=step_id,
        reason="Policy violation",
        policy_ids=["pol-1", "pol-2"],
    )


def _make_approval_response(step_id: str = "step-1-test") -> StepGateResponse:
    return StepGateResponse(
        decision=GateDecision.REQUIRE_APPROVAL,
        step_id=step_id,
        approval_url="https://portal.example.com/approve/step-1",
        reason="Requires manager approval",
    )


def _mock_client() -> AxonFlow:
    """Return a fully-mocked AxonFlow client wired for adapter tests."""
    c = MagicMock(spec=AxonFlow)
    c.create_workflow = AsyncMock(
        return_value=CreateWorkflowResponse(
            workflow_id="wf-test-123",
            workflow_name="test",
            source=WorkflowSource.LANGGRAPH,
            status=WorkflowStatus.IN_PROGRESS,
            created_at=datetime.now(timezone.utc),
        )
    )
    c.step_gate = AsyncMock(return_value=_make_allow_response())
    c.mark_step_completed = AsyncMock(return_value=None)
    c.complete_workflow = AsyncMock(return_value=None)
    c.abort_workflow = AsyncMock(return_value=None)
    return c


# ---------------------------------------------------------------------------
# TestCheckGate
# ---------------------------------------------------------------------------


class TestCheckGate:
    @pytest.fixture
    def client(self) -> AxonFlow:
        return _mock_client()

    @pytest.fixture
    async def adapter(self, client: AxonFlow) -> AxonFlowLangGraphAdapter:
        a = AxonFlowLangGraphAdapter(client, "test-workflow")
        await a.start_workflow()
        return a

    @pytest.mark.asyncio
    async def test_allowed_returns_true(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        result = await adapter.check_gate("plan research", "llm_call", model="gpt-4")
        assert result is True

    @pytest.mark.asyncio
    async def test_blocked_auto_block_true_raises(self, client: AxonFlow) -> None:
        client.step_gate.return_value = _make_block_response("step-1-analyze")
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow", auto_block=True)
        await adapter.start_workflow()

        with pytest.raises(WorkflowBlockedError) as exc_info:
            await adapter.check_gate("analyze", "llm_call")

        assert exc_info.value.step_id == "step-1-analyze"
        assert exc_info.value.reason == "Policy violation"
        assert exc_info.value.policy_ids == ["pol-1", "pol-2"]

    @pytest.mark.asyncio
    async def test_blocked_auto_block_false_returns_false(self, client: AxonFlow) -> None:
        client.step_gate.return_value = _make_block_response()
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow", auto_block=False)
        await adapter.start_workflow()

        result = await adapter.check_gate("analyze", "llm_call")
        assert result is False

    @pytest.mark.asyncio
    async def test_require_approval_raises(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.step_gate.return_value = _make_approval_response("step-1-deploy")

        with pytest.raises(WorkflowApprovalRequiredError) as exc_info:
            await adapter.check_gate("deploy", "llm_call")

        assert exc_info.value.step_id == "step-1-deploy"
        assert exc_info.value.approval_url == "https://portal.example.com/approve/step-1"
        assert exc_info.value.reason == "Requires manager approval"

    @pytest.mark.asyncio
    async def test_step_id_auto_generation(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("Plan Research", "llm_call")
        first_call = client.step_gate.call_args_list[0]
        assert first_call.args[0] == "wf-test-123"
        assert first_call.args[1] == "step-1-plan-research"

        await adapter.check_gate("Execute Code", "tool_call")
        second_call = client.step_gate.call_args_list[1]
        assert second_call.args[1] == "step-2-execute-code"

    @pytest.mark.asyncio
    async def test_step_id_explicit(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("analyze", "llm_call", step_id="my-custom-id")

        client.step_gate.assert_awaited_once()
        assert client.step_gate.call_args.args[1] == "my-custom-id"

    @pytest.mark.asyncio
    async def test_step_type_string_to_enum(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("generate", "llm_call")

        request = client.step_gate.call_args.args[2]
        assert isinstance(request, StepGateRequest)
        assert request.step_type == StepType.LLM_CALL

    @pytest.mark.asyncio
    async def test_tool_context_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        tc = ToolContext(tool_name="web_search", tool_type="function", tool_input={"q": "test"})
        await adapter.check_gate("search", "tool_call", tool_context=tc)

        request = client.step_gate.call_args.args[2]
        assert request.tool_context is tc
        assert request.tool_context.tool_name == "web_search"

    @pytest.mark.asyncio
    async def test_model_provider_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("gen", "llm_call", model="gpt-4o", provider="openai")

        request = client.step_gate.call_args.args[2]
        assert request.model == "gpt-4o"
        assert request.provider == "openai"

    @pytest.mark.asyncio
    async def test_raises_valueerror_if_not_started(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")

        with pytest.raises(ValueError, match="Workflow not started"):
            await adapter.check_gate("step", "llm_call")

    @pytest.mark.asyncio
    async def test_step_input_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        step_input = {"prompt": "Summarize the document", "max_tokens": 500}
        await adapter.check_gate("summarize", "llm_call", step_input=step_input)

        request = client.step_gate.call_args.args[2]
        assert request.step_input == step_input


# ---------------------------------------------------------------------------
# TestStepCompleted
# ---------------------------------------------------------------------------


class TestStepCompleted:
    @pytest.fixture
    def client(self) -> AxonFlow:
        return _mock_client()

    @pytest.fixture
    async def adapter(self, client: AxonFlow) -> AxonFlowLangGraphAdapter:
        a = AxonFlowLangGraphAdapter(client, "test-workflow")
        await a.start_workflow()
        return a

    @pytest.mark.asyncio
    async def test_happy_path(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        # check_gate increments counter to 1
        await adapter.check_gate("analyze", "llm_call")
        await adapter.step_completed("analyze", output={"result": "done"})

        client.mark_step_completed.assert_awaited_once()
        args = client.mark_step_completed.call_args
        assert args.args[0] == "wf-test-123"
        assert args.args[1] == "step-1-analyze"
        request = args.args[2]
        assert isinstance(request, MarkStepCompletedRequest)
        assert request.output == {"result": "done"}

    @pytest.mark.asyncio
    async def test_step_id_matches_counter(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        # First step
        await adapter.check_gate("step-a", "llm_call")
        await adapter.step_completed("step-a")
        first_completed_step_id = client.mark_step_completed.call_args.args[1]
        assert first_completed_step_id == "step-1-step-a"

        # Second step
        await adapter.check_gate("step-b", "llm_call")
        await adapter.step_completed("step-b")
        second_completed_step_id = client.mark_step_completed.call_args.args[1]
        assert second_completed_step_id == "step-2-step-b"

    @pytest.mark.asyncio
    async def test_output_and_metrics_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("gen", "llm_call")
        await adapter.step_completed(
            "gen",
            output={"text": "hello"},
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.005,
        )

        request = client.mark_step_completed.call_args.args[2]
        assert request.output == {"text": "hello"}
        assert request.tokens_in == 100
        assert request.tokens_out == 50
        assert request.cost_usd == 0.005

    @pytest.mark.asyncio
    async def test_explicit_step_id(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.step_completed("analyze", step_id="custom-step-id")

        assert client.mark_step_completed.call_args.args[1] == "custom-step-id"

    @pytest.mark.asyncio
    async def test_raises_valueerror_if_not_started(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")

        with pytest.raises(ValueError, match="Workflow not started"):
            await adapter.step_completed("step")

    @pytest.mark.asyncio
    async def test_metadata_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_gate("gen", "llm_call")
        await adapter.step_completed("gen", metadata={"region": "us-east-1", "retry": 2})

        request = client.mark_step_completed.call_args.args[2]
        assert request.metadata == {"region": "us-east-1", "retry": 2}


# ---------------------------------------------------------------------------
# TestCheckToolGate
# ---------------------------------------------------------------------------


class TestCheckToolGate:
    @pytest.fixture
    def client(self) -> AxonFlow:
        return _mock_client()

    @pytest.fixture
    async def adapter(self, client: AxonFlow) -> AxonFlowLangGraphAdapter:
        a = AxonFlowLangGraphAdapter(client, "test-workflow")
        await a.start_workflow()
        return a

    @pytest.mark.asyncio
    async def test_allowed_returns_true(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        result = await adapter.check_tool_gate("web_search", "function")
        assert result is True

    @pytest.mark.asyncio
    async def test_blocked_raises(self, client: AxonFlow) -> None:
        client.step_gate.return_value = _make_block_response("step-1-tools-web-search")
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow", auto_block=True)
        await adapter.start_workflow()

        with pytest.raises(WorkflowBlockedError) as exc_info:
            await adapter.check_tool_gate("web_search", "function")

        assert exc_info.value.step_id == "step-1-tools-web-search"
        assert exc_info.value.reason == "Policy violation"

    @pytest.mark.asyncio
    async def test_blocked_auto_block_false(self, client: AxonFlow) -> None:
        client.step_gate.return_value = _make_block_response()
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow", auto_block=False)
        await adapter.start_workflow()

        result = await adapter.check_tool_gate("web_search", "function")
        assert result is False

    @pytest.mark.asyncio
    async def test_default_step_name(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("web_search", "function")

        request = client.step_gate.call_args.args[2]
        assert request.step_name == "tools/web_search"

    @pytest.mark.asyncio
    async def test_custom_step_name(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("web_search", "function", step_name="custom-search")

        request = client.step_gate.call_args.args[2]
        assert request.step_name == "custom-search"

    @pytest.mark.asyncio
    async def test_tool_context_built_correctly(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate(
            "web_search",
            "function",
            tool_input={"query": "axonflow docs"},
        )

        request = client.step_gate.call_args.args[2]
        tc = request.tool_context
        assert isinstance(tc, ToolContext)
        assert tc.tool_name == "web_search"
        assert tc.tool_type == "function"
        assert tc.tool_input == {"query": "axonflow docs"}

    @pytest.mark.asyncio
    async def test_tool_type_none(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("web_search", None)

        request = client.step_gate.call_args.args[2]
        assert request.tool_context.tool_type is None

    @pytest.mark.asyncio
    async def test_step_type_is_tool_call(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("web_search", "function")

        request = client.step_gate.call_args.args[2]
        assert request.step_type == StepType.TOOL_CALL

    @pytest.mark.asyncio
    async def test_require_approval_raises(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        client.step_gate.return_value = _make_approval_response()

        with pytest.raises(WorkflowApprovalRequiredError) as exc_info:
            await adapter.check_tool_gate("dangerous_tool", "function")

        assert exc_info.value.approval_url == "https://portal.example.com/approve/step-1"

    @pytest.mark.asyncio
    async def test_raises_valueerror_if_not_started(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")

        with pytest.raises(ValueError, match="Workflow not started"):
            await adapter.check_tool_gate("web_search", "function")


# ---------------------------------------------------------------------------
# TestToolCompleted
# ---------------------------------------------------------------------------


class TestToolCompleted:
    @pytest.fixture
    def client(self) -> AxonFlow:
        return _mock_client()

    @pytest.fixture
    async def adapter(self, client: AxonFlow) -> AxonFlowLangGraphAdapter:
        a = AxonFlowLangGraphAdapter(client, "test-workflow")
        await a.start_workflow()
        return a

    @pytest.mark.asyncio
    async def test_happy_path(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("web_search", "function")
        await adapter.tool_completed("web_search", output={"results": ["r1"]})

        client.mark_step_completed.assert_awaited_once()
        args = client.mark_step_completed.call_args
        assert args.args[0] == "wf-test-123"
        # step_completed uses current counter (1), same step_name -> same step_id
        # sanitization: "/" becomes "-", but "_" stays as-is
        assert args.args[1] == "step-1-tools-web_search"
        assert args.args[2].output == {"results": ["r1"]}

    @pytest.mark.asyncio
    async def test_default_step_name(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("calculator", "function")
        await adapter.tool_completed("calculator")

        completed_step_id = client.mark_step_completed.call_args.args[1]
        assert completed_step_id == "step-1-tools-calculator"

    @pytest.mark.asyncio
    async def test_custom_step_name(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("calc", "function", step_name="math-op")
        await adapter.tool_completed("calc", step_name="math-op")

        completed_step_id = client.mark_step_completed.call_args.args[1]
        assert completed_step_id == "step-1-math-op"

    @pytest.mark.asyncio
    async def test_metrics_forwarded(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        await adapter.check_tool_gate("llm_tool", "function")
        await adapter.tool_completed(
            "llm_tool",
            output={"text": "answer"},
            tokens_in=200,
            tokens_out=100,
            cost_usd=0.01,
        )

        request = client.mark_step_completed.call_args.args[2]
        assert request.tokens_in == 200
        assert request.tokens_out == 100
        assert request.cost_usd == 0.01
        assert request.output == {"text": "answer"}

    @pytest.mark.asyncio
    async def test_step_id_matches_tool_gate(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        # First tool gate increments to 1
        await adapter.check_tool_gate("search", "function")
        # Second tool gate increments to 2
        await adapter.check_tool_gate("summarize", "function")

        # Complete the second tool — counter is at 2
        await adapter.tool_completed("summarize")
        completed_step_id = client.mark_step_completed.call_args.args[1]
        assert completed_step_id == "step-2-tools-summarize"

    @pytest.mark.asyncio
    async def test_raises_valueerror_if_not_started(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")

        with pytest.raises(ValueError, match="Workflow not started"):
            await adapter.tool_completed("web_search")


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.fixture
    def client(self) -> AxonFlow:
        return _mock_client()

    @pytest.mark.asyncio
    async def test_complete_on_success(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")
        await adapter.start_workflow()

        async with adapter:
            pass  # normal exit

        client.complete_workflow.assert_awaited_once_with("wf-test-123")

    @pytest.mark.asyncio
    async def test_abort_on_exception(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")
        await adapter.start_workflow()

        with pytest.raises(RuntimeError):
            async with adapter:
                raise RuntimeError("something broke")

        client.abort_workflow.assert_awaited_once()
        client.complete_workflow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_abort_message_includes_exception(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")
        await adapter.start_workflow()

        with pytest.raises(ValueError):
            async with adapter:
                raise ValueError("bad input data")

        abort_args = client.abort_workflow.call_args
        assert abort_args.args[0] == "wf-test-123"
        assert "Exception: bad input data" in abort_args.args[1]

    @pytest.mark.asyncio
    async def test_no_op_if_not_started(self, client: AxonFlow) -> None:
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")
        # Do NOT call start_workflow — workflow_id is None

        async with adapter:
            pass

        client.complete_workflow.assert_not_awaited()
        client.abort_workflow.assert_not_awaited()
