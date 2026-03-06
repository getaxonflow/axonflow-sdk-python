"""Unit tests for AxonFlowLangGraphAdapter.mcp_tool_interceptor()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from axonflow import AxonFlow
from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter, MCPInterceptorOptions
from axonflow.exceptions import PolicyViolationError
from axonflow.types import MCPCheckInputResponse, MCPCheckOutputResponse  # noqa: TC001

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

    @pytest.mark.asyncio
    async def test_same_connector_type_sent_to_check_output(
        self, adapter: AxonFlowLangGraphAdapter, client: AxonFlow
    ) -> None:
        handler = AsyncMock(return_value="ok")
        request = _make_request(server_name="srv", name="tool")

        await adapter.mcp_tool_interceptor()(request, handler)

        call_kwargs = client.mcp_check_output.call_args.kwargs
        assert call_kwargs["connector_type"] == "srv.tool"

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

        assert result == "[REDACTED]"

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
