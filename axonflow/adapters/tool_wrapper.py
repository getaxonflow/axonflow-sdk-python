"""AxonFlow GovernedTool — framework-agnostic tool governance.

Wraps any LangChain ``BaseTool`` with AxonFlow input/output policy
enforcement. Works transparently with any framework that accepts
``BaseTool`` instances: LangGraph, LangChain AgentExecutor, CrewAI
(via ``from_langchain``), AutoGen (via ``LangChainToolAdapter``).

Example::

    from langchain_core.tools import tool
    from axonflow import AxonFlow
    from axonflow.adapters import govern_tools

    @tool
    def search(query: str) -> str:
        \"\"\"Search the web.\"\"\"
        return web_search(query)

    async with AxonFlow(endpoint="http://localhost:8080") as client:
        governed = govern_tools([search], client)
        # Use governed tools with any framework — they're still BaseTool instances
        result = governed[0].invoke({"query": "latest AI research"})
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import PrivateAttr

from axonflow.exceptions import PolicyViolationError

if TYPE_CHECKING:
    from axonflow import AxonFlow


def _import_base_tool() -> type:
    """Import BaseTool with a helpful error message."""
    try:
        from langchain_core.tools import BaseTool
    except ImportError:
        msg = (
            "langchain-core is required for GovernedTool. "
            "Install it with: pip install 'axonflow[langgraph]'"
        )
        raise ImportError(msg) from None
    else:
        return BaseTool


def _serialize_content(content: Any) -> str:
    """Serialize tool output for policy evaluation."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except (TypeError, ValueError):
        return str(content)


def _make_governed_tool_class() -> type:
    """Create GovernedTool class dynamically to defer BaseTool import.

    This avoids requiring langchain-core at module import time while still
    producing a real BaseTool subclass that passes isinstance checks.
    """
    BaseTool = _import_base_tool()  # noqa: N806

    class GovernedTool(BaseTool):  # type: ignore[misc,valid-type]
        """Wraps a LangChain ``BaseTool`` with AxonFlow input/output governance.

        Every ``invoke`` and ``ainvoke`` call runs through two policy checks:

        1. **Input check** (``mcp_check_input``): evaluates tool arguments before
           execution. Blocked calls raise ``PolicyViolationError`` and the tool
           never runs.
        2. **Output check** (``mcp_check_output``): evaluates tool results after
           execution. Can block (raise), redact (return cleaned data), or allow.

        ``GovernedTool`` is a ``BaseTool`` subclass, so any framework that accepts
        tools (LangGraph ``ToolNode``, LangChain ``AgentExecutor``, CrewAI via
        ``from_langchain``, AutoGen via ``LangChainToolAdapter``) can consume it
        without changes.

        Args:
            tool: The ``BaseTool`` instance to wrap.
            client: An authenticated :class:`axonflow.AxonFlow` client.
            connector_type_fn: Optional callable mapping a tool name to a
                connector type string. Defaults to the tool's ``name``.
            operation: Operation type for input checks. ``"execute"`` (default)
                or ``"query"`` for read-only tools.
        """

        name: str = ""
        description: str = ""

        _wrapped: Any = PrivateAttr()
        _client: Any = PrivateAttr()
        _connector_type: str = PrivateAttr()
        _operation: str = PrivateAttr(default="execute")

        def __init__(
            self,
            tool: Any,
            client: AxonFlow,
            *,
            connector_type_fn: Callable[[str], str] | None = None,
            operation: str = "execute",
        ) -> None:
            if not isinstance(tool, BaseTool):
                msg = f"tool must be a BaseTool instance, got {type(tool)}"
                raise TypeError(msg)

            # Copy all public BaseTool configuration to preserve behavior
            super().__init__(
                name=tool.name,  # type: ignore[attr-defined]
                description=tool.description,  # type: ignore[attr-defined]
                args_schema=tool.args_schema,  # type: ignore[attr-defined]
                return_direct=getattr(tool, "return_direct", False),
                response_format=getattr(tool, "response_format", "content"),
                tags=getattr(tool, "tags", None),
                metadata=getattr(tool, "metadata", None),
                handle_tool_error=getattr(tool, "handle_tool_error", False),
                handle_validation_error=getattr(tool, "handle_validation_error", False),
            )
            self._wrapped = tool
            self._client = client
            self._connector_type = (
                connector_type_fn(tool.name) if connector_type_fn else tool.name  # type: ignore[attr-defined]
            )
            self._operation = operation

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            msg = "GovernedTool overrides invoke/ainvoke directly"
            raise NotImplementedError(msg)

        @staticmethod
        def _run_coro_sync(coro: Any) -> Any:
            """Run a coroutine synchronously.

            Only works when called outside an async context (no running event loop).
            For async contexts, use ``ainvoke()`` instead.
            """
            try:
                asyncio.get_running_loop()
                # Can't safely run async client methods from inside an event loop —
                # the httpx AsyncClient is bound to the caller's loop
                msg = (
                    "GovernedTool.invoke() cannot be called from within an async "
                    "context (running event loop detected). Use ainvoke() instead, "
                    "or use SyncAxonFlow as the client."
                )
                raise RuntimeError(msg)
            except RuntimeError as e:
                if "invoke()" in str(e):
                    raise
                # No running loop — safe to use asyncio.run
                return asyncio.run(coro)

        def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            """Invoke with synchronous input/output governance."""
            statement = json.dumps(input, default=str) if not isinstance(input, str) else input

            input_check = self._run_coro_sync(
                self._client.mcp_check_input(
                    connector_type=self._connector_type,
                    statement=statement,
                    operation=self._operation,
                )
            )
            if not input_check.allowed:
                raise PolicyViolationError(
                    input_check.block_reason or "Tool call blocked by input policy"
                )

            result = self._wrapped.invoke(input, config, **kwargs)

            serialized = _serialize_content(result)
            output_check = self._run_coro_sync(
                self._client.mcp_check_output(
                    connector_type=self._connector_type,
                    message=serialized,
                )
            )
            if not output_check.allowed:
                raise PolicyViolationError(
                    output_check.block_reason or "Tool output blocked by policy"
                )
            if output_check.redacted_data is not None:
                return output_check.redacted_data

            return result

        async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            """Invoke with async input/output governance."""
            statement = json.dumps(input, default=str) if not isinstance(input, str) else input

            input_check = await self._client.mcp_check_input(
                connector_type=self._connector_type,
                statement=statement,
                operation=self._operation,
            )
            if not input_check.allowed:
                raise PolicyViolationError(
                    input_check.block_reason or "Tool call blocked by input policy"
                )

            result = await self._wrapped.ainvoke(input, config, **kwargs)

            serialized = _serialize_content(result)
            output_check = await self._client.mcp_check_output(
                connector_type=self._connector_type,
                message=serialized,
            )
            if not output_check.allowed:
                raise PolicyViolationError(
                    output_check.block_reason or "Tool output blocked by policy"
                )
            if output_check.redacted_data is not None:
                return output_check.redacted_data

            return result

        def __repr__(self) -> str:
            return f"GovernedTool(name={self.name!r}, connector_type={self._connector_type!r})"

    return GovernedTool


# Create the class — deferred until first import of this module.
# If langchain-core is not installed, importing this module will raise ImportError
# with a clear message.
GovernedTool = _make_governed_tool_class()


def govern_tools(
    tools: list[Any],
    client: AxonFlow,
    *,
    connector_type_fn: Callable[[str], str] | None = None,
    operation: str = "execute",
) -> list[Any]:
    """Wrap a list of tools with AxonFlow governance.

    One-liner to govern all tools before passing them to any framework::

        governed = govern_tools([search, calculator], client)
        agent = AgentExecutor(agent=agent, tools=governed)

    Args:
        tools: List of ``BaseTool`` instances to wrap.
        client: An authenticated :class:`axonflow.AxonFlow` client.
        connector_type_fn: Optional callable mapping tool names to connector
            type strings. Defaults to using each tool's ``name``.
        operation: Operation type for input checks (``"execute"`` or ``"query"``).

    Returns:
        List of ``GovernedTool`` instances (which are ``BaseTool`` subclasses).
    """
    return [
        GovernedTool(
            t,
            client,
            connector_type_fn=connector_type_fn,
            operation=operation,
        )
        for t in tools
    ]
