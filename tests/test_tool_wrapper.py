"""Unit tests for GovernedTool and govern_tools."""

from __future__ import annotations

import json
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from axonflow.adapters.tool_wrapper import GovernedTool, govern_tools
from axonflow.exceptions import PolicyViolationError
from axonflow.types import MCPCheckInputResponse, MCPCheckOutputResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _input_allowed() -> MCPCheckInputResponse:
    return MCPCheckInputResponse(allowed=True, policies_evaluated=1)


def _input_blocked(reason: str = "Blocked by policy") -> MCPCheckInputResponse:
    return MCPCheckInputResponse(allowed=False, block_reason=reason, policies_evaluated=1)


def _output_allowed(redacted_data: Any = None) -> MCPCheckOutputResponse:
    return MCPCheckOutputResponse(allowed=True, policies_evaluated=1, redacted_data=redacted_data)


def _output_blocked(reason: str = "Output blocked") -> MCPCheckOutputResponse:
    return MCPCheckOutputResponse(allowed=False, block_reason=reason, policies_evaluated=1)


def _make_client(
    input_response: MCPCheckInputResponse | None = None,
    output_response: MCPCheckOutputResponse | None = None,
) -> MagicMock:
    client = MagicMock()
    client.mcp_check_input = AsyncMock(return_value=input_response or _input_allowed())
    client.mcp_check_output = AsyncMock(return_value=output_response or _output_allowed())

    import asyncio

    def _run_sync(coro: Any) -> Any:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    client._run_sync = _run_sync
    return client


def _make_tool(name: str = "search", result: str = "tool output") -> MagicMock:
    """Create a mock BaseTool."""
    try:
        from langchain_core.tools import BaseTool
    except ImportError:
        pytest.skip("langchain-core not installed")

    tool = MagicMock(spec=BaseTool)
    tool.name = name
    tool.description = f"A {name} tool"
    tool.args_schema = None
    tool.invoke = MagicMock(return_value=result)
    tool.ainvoke = AsyncMock(return_value=result)
    return tool


def _make_governed(
    name: str = "search",
    result: str = "tool output",
    input_response: MCPCheckInputResponse | None = None,
    output_response: MCPCheckOutputResponse | None = None,
    connector_type_fn: Any = None,
    operation: str = "execute",
) -> tuple[GovernedTool, MagicMock, MagicMock]:
    """Create a GovernedTool with mock tool and client. Returns (governed, tool, client)."""
    tool = _make_tool(name, result)
    client = _make_client(input_response, output_response)
    governed = GovernedTool(
        tool,
        client,
        connector_type_fn=connector_type_fn,
        operation=operation,
    )
    return governed, tool, client


# ---------------------------------------------------------------------------
# TestGovernedToolInit
# ---------------------------------------------------------------------------


class TestGovernedToolInit:
    def test_copies_name_and_description(self) -> None:
        governed, _tool, _ = _make_governed(name="web_search")
        assert governed.name == "web_search"
        assert governed.description == "A web_search tool"

    def test_is_base_tool_instance(self) -> None:
        try:
            from langchain_core.tools import BaseTool
        except ImportError:
            pytest.skip("langchain-core not installed")
        governed, _, _ = _make_governed()
        assert isinstance(governed, BaseTool)

    def test_default_connector_type_is_tool_name(self) -> None:
        governed, _, _ = _make_governed(name="database_query")
        assert governed._connector_type == "database_query"

    def test_custom_connector_type_fn(self) -> None:
        governed, _, _ = _make_governed(
            name="get_user",
            connector_type_fn=lambda n: f"crm.{n}",
        )
        assert governed._connector_type == "crm.get_user"

    def test_operation_default(self) -> None:
        governed, _, _ = _make_governed()
        assert governed._operation == "execute"

    def test_operation_custom(self) -> None:
        governed, _, _ = _make_governed(operation="query")
        assert governed._operation == "query"

    def test_rejects_non_base_tool(self) -> None:
        client = _make_client()
        with pytest.raises(TypeError, match="must be a BaseTool instance"):
            GovernedTool("not a tool", client)

    def test_repr(self) -> None:
        governed, _, _ = _make_governed(name="search")
        assert repr(governed) == "GovernedTool(name='search', connector_type='search')"

    def test_repr_custom_connector_type(self) -> None:
        governed, _, _ = _make_governed(
            name="lookup",
            connector_type_fn=lambda n: f"salesforce.{n}",
        )
        assert "connector_type='salesforce.lookup'" in repr(governed)


# ---------------------------------------------------------------------------
# TestGovernedToolInvoke
# ---------------------------------------------------------------------------


class TestGovernedToolInvoke:
    def test_allowed_returns_result(self) -> None:
        governed, tool, _client = _make_governed(result="clean data")
        result = governed.invoke({"query": "hello"})
        assert result == "clean data"
        tool.invoke.assert_called_once()

    def test_calls_mcp_check_input(self) -> None:
        governed, _, client = _make_governed()
        governed.invoke({"query": "test"})
        client.mcp_check_input.assert_awaited_once()
        call_kwargs = client.mcp_check_input.call_args.kwargs
        assert call_kwargs["connector_type"] == "search"
        assert call_kwargs["operation"] == "execute"

    def test_calls_mcp_check_output(self) -> None:
        governed, _, client = _make_governed(result="some output")
        governed.invoke({"query": "test"})
        client.mcp_check_output.assert_awaited_once()
        call_kwargs = client.mcp_check_output.call_args.kwargs
        assert call_kwargs["connector_type"] == "search"
        assert call_kwargs["message"] == "some output"

    def test_serializes_dict_input_as_json(self) -> None:
        governed, _, client = _make_governed()
        governed.invoke({"query": "test", "limit": 10})
        statement = client.mcp_check_input.call_args.kwargs["statement"]
        parsed = json.loads(statement)
        assert parsed == {"query": "test", "limit": 10}

    def test_string_input_passed_directly(self) -> None:
        governed, _, client = _make_governed()
        governed.invoke("plain text query")
        statement = client.mcp_check_input.call_args.kwargs["statement"]
        assert statement == "plain text query"

    def test_passes_config_to_wrapped_tool(self) -> None:
        governed, tool, _ = _make_governed()
        config = {"configurable": {"user_token": "tok"}}
        governed.invoke({"query": "test"}, config=config)
        tool.invoke.assert_called_once_with({"query": "test"}, config)


# ---------------------------------------------------------------------------
# TestGovernedToolInputBlocked
# ---------------------------------------------------------------------------


class TestGovernedToolInputBlocked:
    def test_raises_policy_violation(self) -> None:
        governed, _, _ = _make_governed(input_response=_input_blocked("PII detected in query"))
        with pytest.raises(PolicyViolationError, match="PII detected in query"):
            governed.invoke({"query": "SSN 123-45-6789"})

    def test_tool_never_executes(self) -> None:
        governed, tool, _ = _make_governed(input_response=_input_blocked("Blocked"))
        with pytest.raises(PolicyViolationError):
            governed.invoke({"query": "blocked"})
        tool.invoke.assert_not_called()

    def test_output_check_never_called(self) -> None:
        governed, _, client = _make_governed(input_response=_input_blocked("Blocked"))
        with pytest.raises(PolicyViolationError):
            governed.invoke({"query": "blocked"})
        client.mcp_check_output.assert_not_awaited()

    def test_fallback_message_when_no_reason(self) -> None:
        governed, _, _ = _make_governed(
            input_response=MCPCheckInputResponse(
                allowed=False, block_reason=None, policies_evaluated=1
            )
        )
        with pytest.raises(PolicyViolationError, match="Tool call blocked by input policy"):
            governed.invoke({"query": "test"})


# ---------------------------------------------------------------------------
# TestGovernedToolOutputBlocked
# ---------------------------------------------------------------------------


class TestGovernedToolOutputBlocked:
    def test_raises_policy_violation(self) -> None:
        governed, _, _ = _make_governed(output_response=_output_blocked("Exfiltration detected"))
        with pytest.raises(PolicyViolationError, match="Exfiltration detected"):
            governed.invoke({"query": "test"})

    def test_tool_still_executes(self) -> None:
        governed, tool, _ = _make_governed(output_response=_output_blocked("Blocked"))
        with pytest.raises(PolicyViolationError):
            governed.invoke({"query": "test"})
        tool.invoke.assert_called_once()

    def test_fallback_message_when_no_reason(self) -> None:
        governed, _, _ = _make_governed(
            output_response=MCPCheckOutputResponse(
                allowed=False, block_reason=None, policies_evaluated=1
            )
        )
        with pytest.raises(PolicyViolationError, match="Tool output blocked by policy"):
            governed.invoke({"query": "test"})


# ---------------------------------------------------------------------------
# TestGovernedToolOutputRedacted
# ---------------------------------------------------------------------------


class TestGovernedToolOutputRedacted:
    def test_returns_redacted_data(self) -> None:
        governed, _, _ = _make_governed(
            result="Name: John, SSN: 123-45-6789",
            output_response=_output_allowed(redacted_data="Name: John, SSN: ***-**-6789"),
        )
        result = governed.invoke({"query": "lookup"})
        assert result == "Name: John, SSN: ***-**-6789"

    def test_original_not_returned(self) -> None:
        governed, _, _ = _make_governed(
            result="SENSITIVE DATA",
            output_response=_output_allowed(redacted_data="[REDACTED]"),
        )
        result = governed.invoke({"query": "test"})
        assert result != "SENSITIVE DATA"
        assert result == "[REDACTED]"


# ---------------------------------------------------------------------------
# TestGovernedToolAinvoke
# ---------------------------------------------------------------------------


class TestGovernedToolAinvoke:
    @pytest.mark.asyncio
    async def test_allowed_returns_result(self) -> None:
        governed, tool, _ = _make_governed(result="async result")
        result = await governed.ainvoke({"query": "test"})
        assert result == "async result"
        tool.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_input_blocked_raises(self) -> None:
        governed, tool, _ = _make_governed(input_response=_input_blocked("Async block"))
        with pytest.raises(PolicyViolationError, match="Async block"):
            await governed.ainvoke({"query": "test"})
        tool.ainvoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_output_redacted(self) -> None:
        governed, _, _ = _make_governed(
            result="raw data",
            output_response=_output_allowed(redacted_data="clean data"),
        )
        result = await governed.ainvoke({"query": "test"})
        assert result == "clean data"

    @pytest.mark.asyncio
    async def test_output_blocked_raises(self) -> None:
        governed, tool, _ = _make_governed(output_response=_output_blocked("Async output block"))
        with pytest.raises(PolicyViolationError, match="Async output block"):
            await governed.ainvoke({"query": "test"})
        tool.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_async_mcp_check_directly(self) -> None:
        governed, _, client = _make_governed()
        await governed.ainvoke({"query": "test"})
        client.mcp_check_input.assert_awaited_once()
        client.mcp_check_output.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestGovernedToolContentSerialization
# ---------------------------------------------------------------------------


class TestGovernedToolContentSerialization:
    def test_string_result_passed_as_message(self) -> None:
        governed, _, client = _make_governed(result="plain string")
        governed.invoke({"query": "test"})
        msg = client.mcp_check_output.call_args.kwargs["message"]
        assert msg == "plain string"

    def test_dict_result_json_serialized(self) -> None:
        governed, _, client = _make_governed(result={"key": "value", "count": 42})
        governed.invoke({"query": "test"})
        msg = client.mcp_check_output.call_args.kwargs["message"]
        parsed = json.loads(msg)
        assert parsed == {"key": "value", "count": 42}

    def test_list_result_json_serialized(self) -> None:
        governed, _, client = _make_governed(result=["a", "b", "c"])
        governed.invoke({"query": "test"})
        msg = client.mcp_check_output.call_args.kwargs["message"]
        assert json.loads(msg) == ["a", "b", "c"]

    def test_non_serializable_falls_back_to_str(self) -> None:
        obj = object()
        governed, _, client = _make_governed(result=obj)
        governed.invoke({"query": "test"})
        msg = client.mcp_check_output.call_args.kwargs["message"]
        assert "object at" in msg


# ---------------------------------------------------------------------------
# TestGovernedToolCustomOptions
# ---------------------------------------------------------------------------


class TestGovernedToolCustomOptions:
    def test_custom_connector_type_in_input_check(self) -> None:
        governed, _, client = _make_governed(
            name="get_user",
            connector_type_fn=lambda n: f"salesforce.{n}",
        )
        governed.invoke({"id": "123"})
        ct = client.mcp_check_input.call_args.kwargs["connector_type"]
        assert ct == "salesforce.get_user"

    def test_custom_connector_type_in_output_check(self) -> None:
        governed, _, client = _make_governed(
            name="get_user",
            connector_type_fn=lambda n: f"salesforce.{n}",
        )
        governed.invoke({"id": "123"})
        ct = client.mcp_check_output.call_args.kwargs["connector_type"]
        assert ct == "salesforce.get_user"

    def test_query_operation(self) -> None:
        governed, _, client = _make_governed(operation="query")
        governed.invoke({"query": "test"})
        op = client.mcp_check_input.call_args.kwargs["operation"]
        assert op == "query"


# ---------------------------------------------------------------------------
# TestGovernToolsHelper
# ---------------------------------------------------------------------------


class TestGovernToolsHelper:
    def test_wraps_multiple_tools(self) -> None:
        tools = [_make_tool("search"), _make_tool("calculator")]
        client = _make_client()
        governed = govern_tools(tools, client)
        assert len(governed) == 2
        assert governed[0].name == "search"
        assert governed[1].name == "calculator"

    def test_all_are_governed_tools(self) -> None:
        tools = [_make_tool("a"), _make_tool("b")]
        client = _make_client()
        governed = govern_tools(tools, client)
        for g in governed:
            assert isinstance(g, GovernedTool)

    def test_passes_options_through(self) -> None:
        tools = [_make_tool("search")]
        client = _make_client()
        governed = govern_tools(
            tools,
            client,
            connector_type_fn=lambda n: f"custom.{n}",
            operation="query",
        )
        assert governed[0]._connector_type == "custom.search"
        assert governed[0]._operation == "query"

    def test_empty_list(self) -> None:
        governed = govern_tools([], _make_client())
        assert governed == []


# ---------------------------------------------------------------------------
# TestMCPToolInterceptorDeprecation
# ---------------------------------------------------------------------------


class TestMCPToolInterceptorDeprecation:
    def test_deprecation_warning_fires(self) -> None:
        from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter

        client = MagicMock()
        adapter = AxonFlowLangGraphAdapter(client, "test-workflow")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import contextlib  # noqa: PLC0415

            with contextlib.suppress(ImportError):
                adapter.mcp_tool_interceptor()

            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) >= 1
            assert "deprecated" in str(deprecation_warnings[0].message).lower()
            assert "GovernedTool" in str(deprecation_warnings[0].message)
