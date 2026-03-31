"""LangGraph Adapter for AxonFlow Workflow Control Plane.

This adapter wraps LangGraph workflows with AxonFlow governance gates,
providing policy enforcement at step transitions.

"LangGraph runs the workflow. AxonFlow decides when it's allowed to move forward."

Example:
    >>> from langgraph.graph import StateGraph
    >>> from axonflow import AxonFlow
    >>> from axonflow.adapters import AxonFlowLangGraphAdapter
    >>>
    >>> # Create your LangGraph workflow
    >>> graph = StateGraph(...)
    >>>
    >>> # Wrap with AxonFlow governance
    >>> async with AxonFlow(endpoint="http://localhost:8080") as client:
    ...     adapter = AxonFlowLangGraphAdapter(client, "my-workflow")
    ...
    ...     # Start workflow and register with AxonFlow
    ...     await adapter.start_workflow()
    ...
    ...     # Before each step, check the gate
    ...     if await adapter.check_gate("generate_code", "llm_call", model="gpt-4"):
    ...         # Execute the step
    ...         result = await execute_step()
    ...         await adapter.step_completed("generate_code")
    ...
    ...     # Complete workflow
    ...     await adapter.complete_workflow()
"""

from __future__ import annotations

import asyncio
import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from axonflow.exceptions import PolicyViolationError
from axonflow.workflow import (
    ApprovalStatus,
    CreateWorkflowRequest,
    GateDecision,
    MarkStepCompletedRequest,
    StepGateRequest,
    StepType,
    ToolContext,
    WorkflowSource,
)

if TYPE_CHECKING:
    from axonflow import AxonFlow


class WorkflowBlockedError(Exception):
    """Raised when a workflow step is blocked by policy."""

    def __init__(
        self,
        message: str,
        step_id: str | None = None,
        reason: str | None = None,
        policy_ids: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.step_id = step_id
        self.reason = reason
        self.policy_ids = policy_ids or []


class WorkflowApprovalRequiredError(Exception):
    """Raised when a workflow step requires approval."""

    def __init__(
        self,
        message: str,
        step_id: str | None = None,
        approval_url: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step_id = step_id
        self.approval_url = approval_url
        self.reason = reason


@dataclass
class MCPInterceptorOptions:
    """Options for :meth:`AxonFlowLangGraphAdapter.mcp_tool_interceptor`.

    Attributes:
        connector_type_fn: Optional callable that maps an MCP request to a
            connector type string. Defaults to ``"{server_name}.{tool_name}"``.
        operation: Operation type passed to ``mcp_check_input``. Defaults to
            ``"execute"``. Set to ``"query"`` for known read-only tool calls.
    """

    connector_type_fn: Callable[[Any], str] | None = field(default=None)
    operation: str = field(default="execute")


class AxonFlowLangGraphAdapter:
    """Wraps LangGraph workflows with AxonFlow governance gates.

    This adapter provides a simple interface for integrating AxonFlow's
    Workflow Control Plane with LangGraph workflows. It handles workflow
    registration, step gate checks, and workflow lifecycle management.

    Attributes:
        client: AxonFlow client instance
        workflow_name: Name of the workflow
        workflow_id: ID assigned after workflow creation (None until started)
        source: Workflow source (defaults to langgraph)

    Example:
        >>> adapter = AxonFlowLangGraphAdapter(client, "code-review-pipeline")
        >>> await adapter.start_workflow()
        >>>
        >>> # Before each LangGraph node execution
        >>> if await adapter.check_gate("analyze", "llm_call"):
        ...     result = await analyze_code(state)
        ...     await adapter.step_completed("analyze")
    """

    def __init__(
        self,
        client: AxonFlow,
        workflow_name: str,
        source: WorkflowSource = WorkflowSource.LANGGRAPH,
        *,
        auto_block: bool = True,
    ) -> None:
        """Initialize the LangGraph adapter.

        Args:
            client: AxonFlow client instance
            workflow_name: Human-readable name for the workflow
            source: Workflow source (defaults to langgraph)
            auto_block: If True, check_gate raises WorkflowBlockedError on block
                       If False, returns False and caller handles it
        """
        self.client = client
        self.workflow_name = workflow_name
        self.source = source
        self.workflow_id: str | None = None
        self._step_counter = 0
        self._auto_block = auto_block

    async def start_workflow(
        self,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Register the workflow with AxonFlow.

        Call this at the start of your LangGraph workflow execution.

        Args:
            metadata: Additional workflow metadata
            trace_id: External trace ID for correlation (Langsmith, Datadog, OTel)

        Returns:
            The assigned workflow ID

        Example:
            >>> workflow_id = await adapter.start_workflow(
            ...     metadata={"customer_id": "cust-123"},
            ...     trace_id="langsmith-run-abc123",
            ... )
        """
        request = CreateWorkflowRequest(
            workflow_name=self.workflow_name,
            source=self.source,
            metadata=metadata or {},
            trace_id=trace_id,
        )

        response = await self.client.create_workflow(request)
        self.workflow_id = response.workflow_id
        return self.workflow_id

    async def check_gate(
        self,
        step_name: str,
        step_type: str | StepType,
        *,
        step_id: str | None = None,
        step_input: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> bool:
        """Check if a step is allowed to proceed.

        Call this before executing each LangGraph node to check policy approval.

        Args:
            step_name: Human-readable step name
            step_type: Type of step (llm_call, tool_call, connector_call, human_task)
            step_id: Optional step ID (auto-generated if not provided)
            step_input: Input data for the step (for policy evaluation)
            model: LLM model being used
            provider: LLM provider being used
            tool_context: Tool-level context for per-tool governance (tool_call steps)

        Returns:
            True if step is allowed, False if blocked (when auto_block=False)

        Raises:
            WorkflowBlockedError: If step is blocked and auto_block=True
            WorkflowApprovalRequiredError: If step requires approval
            ValueError: If workflow not started

        Example:
            >>> if await adapter.check_gate("generate", "llm_call", model="gpt-4"):
            ...     result = await generate_code(state)
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        # Convert string to StepType if needed
        if isinstance(step_type, str):
            step_type = StepType(step_type)

        # Generate step ID if not provided
        if step_id is None:
            self._step_counter += 1
            safe_name = step_name.lower().replace(" ", "-").replace("/", "-")
            step_id = f"step-{self._step_counter}-{safe_name}"

        request = StepGateRequest(
            step_name=step_name,
            step_type=step_type,
            step_input=step_input or {},
            model=model,
            provider=provider,
            tool_context=tool_context,
        )

        response = await self.client.step_gate(self.workflow_id, step_id, request)

        if response.decision == GateDecision.BLOCK:
            if self._auto_block:
                msg = f"Step '{step_name}' blocked: {response.reason}"
                raise WorkflowBlockedError(
                    msg,
                    step_id=response.step_id,
                    reason=response.reason,
                    policy_ids=response.policy_ids,
                )
            return False

        if response.decision == GateDecision.REQUIRE_APPROVAL:
            msg = f"Step '{step_name}' requires approval"
            raise WorkflowApprovalRequiredError(
                msg,
                step_id=response.step_id,
                approval_url=response.approval_url,
                reason=response.reason,
            )

        return True

    async def step_completed(
        self,
        step_name: str,
        *,
        step_id: str | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Mark a step as completed.

        Call this after successfully executing a LangGraph node.

        Args:
            step_name: Step name (used to generate step_id if not provided)
            step_id: Optional step ID (must match the one used in check_gate)
            output: Output data from the step
            metadata: Additional metadata
            tokens_in: Input tokens consumed
            tokens_out: Output tokens produced
            cost_usd: Cost in USD

        Example:
            >>> await adapter.step_completed("generate", output={"code": result})
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        # Generate step ID if not provided (must match check_gate)
        if step_id is None:
            safe_name = step_name.lower().replace(" ", "-").replace("/", "-")
            step_id = f"step-{self._step_counter}-{safe_name}"

        request = MarkStepCompletedRequest(
            output=output or {},
            metadata=metadata or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

        await self.client.mark_step_completed(self.workflow_id, step_id, request)

    async def check_tool_gate(
        self,
        tool_name: str,
        tool_type: str | None = None,
        *,
        step_name: str | None = None,
        step_id: str | None = None,
        tool_input: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> bool:
        """Check if a specific tool invocation is allowed.

        Convenience wrapper around check_gate() that sets step_type=TOOL_CALL
        and includes ToolContext for per-tool governance.

        Args:
            tool_name: Name of the tool being invoked
            tool_type: Tool type (function, mcp, api)
            step_name: Step name (defaults to "tools/{tool_name}")
            step_id: Optional step ID (auto-generated if not provided)
            tool_input: Input arguments for the tool
            model: LLM model being used
            provider: LLM provider being used

        Returns:
            True if tool invocation is allowed, False if blocked (when auto_block=False)

        Raises:
            WorkflowBlockedError: If tool is blocked and auto_block=True
            WorkflowApprovalRequiredError: If tool requires approval
            ValueError: If workflow not started

        Example:
            >>> if await adapter.check_tool_gate("web_search", "function",
            ...     tool_input={"query": "latest news"}):
            ...     result = await web_search(query="latest news")
            ...     await adapter.tool_completed("web_search", output={"results": result})
        """
        if step_name is None:
            step_name = f"tools/{tool_name}"

        tool_context = ToolContext(
            tool_name=tool_name,
            tool_type=tool_type,
            tool_input=tool_input or {},
        )

        return await self.check_gate(
            step_name=step_name,
            step_type=StepType.TOOL_CALL,
            step_id=step_id,
            model=model,
            provider=provider,
            tool_context=tool_context,
        )

    async def tool_completed(
        self,
        tool_name: str,
        *,
        step_name: str | None = None,
        step_id: str | None = None,
        output: dict[str, Any] | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Mark a tool invocation as completed.

        Convenience wrapper around step_completed() for tool-level tracking.

        Args:
            tool_name: Name of the tool that was invoked
            step_name: Step name (defaults to "tools/{tool_name}")
            step_id: Optional step ID (must match the one used in check_tool_gate)
            output: Output data from the tool
            tokens_in: Input tokens consumed
            tokens_out: Output tokens produced
            cost_usd: Cost in USD

        Example:
            >>> await adapter.tool_completed("web_search",
            ...     output={"results": search_results},
            ...     tokens_in=150,
            ...     tokens_out=500,
            ...     cost_usd=0.002,
            ... )
        """
        if step_name is None:
            step_name = f"tools/{tool_name}"

        await self.step_completed(
            step_name=step_name,
            step_id=step_id,
            output=output,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    async def complete_workflow(self) -> None:
        """Mark the workflow as completed.

        Call this when your LangGraph workflow finishes successfully.

        Example:
            >>> await adapter.complete_workflow()
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        await self.client.complete_workflow(self.workflow_id)

    async def abort_workflow(self, reason: str | None = None) -> None:
        """Abort the workflow.

        Call this when your LangGraph workflow fails or is cancelled.

        Args:
            reason: Reason for aborting

        Example:
            >>> await adapter.abort_workflow("User cancelled the operation")
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        await self.client.abort_workflow(self.workflow_id, reason)

    async def fail_workflow(self, reason: str | None = None) -> None:
        """Fail the workflow.

        Call this when your LangGraph workflow has encountered an unrecoverable error.

        Args:
            reason: Reason for the failure

        Example:
            >>> await adapter.fail_workflow("Pipeline stage crashed")
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        await self.client.fail_workflow(self.workflow_id, reason)

    async def wait_for_approval(
        self,
        step_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
    ) -> bool:
        """Wait for a step to be approved.

        Poll the workflow status until the step is approved or rejected.

        Args:
            step_id: Step ID to wait for
            poll_interval: Seconds between polls
            timeout: Maximum seconds to wait

        Returns:
            True if approved, False if rejected

        Raises:
            TimeoutError: If approval not received within timeout
        """
        if not self.workflow_id:
            msg = "Workflow not started. Call start_workflow() first."
            raise ValueError(msg)

        elapsed = 0.0
        while elapsed < timeout:
            status = await self.client.get_workflow(self.workflow_id)

            # Find the step
            for step in status.steps:
                if step.step_id == step_id:
                    if step.approval_status:
                        if step.approval_status == ApprovalStatus.APPROVED:
                            return True
                        if step.approval_status == ApprovalStatus.REJECTED:
                            return False
                    break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        msg = f"Approval timeout after {timeout}s for step {step_id}"
        raise TimeoutError(msg)

    def mcp_tool_interceptor(
        self,
        options: MCPInterceptorOptions | None = None,
    ) -> Callable[..., Any]:
        """Return an async MCP tool interceptor for use with MultiServerMCPClient.

        The interceptor enforces AxonFlow input and output policies around every
        MCP tool call. Pass the result directly to MultiServerMCPClient's
        ``tool_interceptors`` parameter:

        Example:
            >>> mcp_client = MultiServerMCPClient(
            ...     {"my-server": {"url": "...", "transport": "http"}},
            ...     tool_interceptors=[adapter.mcp_tool_interceptor()],
            ... )

        With custom options:

        Example:
            >>> opts = MCPInterceptorOptions(
            ...     connector_type_fn=lambda req: req.server_name,
            ...     operation="query",
            ... )
            >>> tool_interceptors=[adapter.mcp_tool_interceptor(opts)]

        Args:
            options: Optional :class:`MCPInterceptorOptions` controlling connector
                type derivation and operation type. Uses defaults if not provided.

        Returns:
            An async callable ``(request, handler) -> result`` suitable for
            ``MultiServerMCPClient(tool_interceptors=[...])``.

        .. deprecated::
            Use :class:`~axonflow.adapters.tool_wrapper.GovernedTool` (any framework)
            or :meth:`tool_output_wrapper` (LangGraph ``ToolNode``) instead.
            ``mcp_tool_interceptor`` will be removed after April 15, 2026.
        """
        warnings.warn(
            "mcp_tool_interceptor() is deprecated and will be removed after "
            "April 15, 2026. Use GovernedTool (any framework) or "
            "tool_output_wrapper() (LangGraph ToolNode) instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            from mcp.types import CallToolResult, TextContent  # noqa: PLC0415
        except ImportError as exc:
            msg = (
                "The 'mcp' package is required to use mcp_tool_interceptor. "
                "Install it with: pip install 'axonflow[langgraph]'"
            )
            raise ImportError(msg) from exc

        opts = options or MCPInterceptorOptions()

        def _default_connector_type(request: Any) -> str:
            return f"{request.server_name}.{request.name}"

        resolve_connector_type = opts.connector_type_fn or _default_connector_type

        async def _interceptor(request: Any, handler: Callable[..., Any]) -> Any:
            connector_type = resolve_connector_type(request)
            args_str = json.dumps(request.args, default=str) if request.args else "{}"
            statement = f"{connector_type}({args_str})"

            pre_check = await self.client.mcp_check_input(
                connector_type=connector_type,
                statement=statement,
                operation=opts.operation,
                parameters=request.args,
            )
            if not pre_check.allowed:
                raise PolicyViolationError(pre_check.block_reason or "Tool call blocked by policy")

            result = await handler(request)

            try:
                result_str = json.dumps(result, default=str)
            except (TypeError, ValueError):
                result_str = str(result)
            output_check = await self.client.mcp_check_output(
                connector_type=connector_type,
                message=result_str,
            )
            if not output_check.allowed:
                raise PolicyViolationError(
                    output_check.block_reason or "Tool result blocked by policy"
                )
            if output_check.redacted_data is not None:
                return CallToolResult(
                    content=[TextContent(type="text", text=output_check.redacted_data)]
                )

            return result

        return _interceptor

    def tool_output_wrapper(
        self,
        options: MCPInterceptorOptions | None = None,
    ) -> Callable[..., Any]:
        """Return an async tool-call wrapper for use with LangGraph's ``ToolNode``.

        The wrapper enforces AxonFlow input and output policies on **every** tool
        invocation — including locally defined ``@tool`` functions that bypass
        the MCP interceptor.  Pass the result directly to ``ToolNode``'s
        ``awrap_tool_call`` parameter:

        Example:
            >>> wrapper = adapter.tool_output_wrapper()
            >>> tool_node = ToolNode(tools, awrap_tool_call=wrapper)

        With custom options:

        Example:
            >>> opts = MCPInterceptorOptions(
            ...     connector_type_fn=lambda call: f"local.{call['name']}",
            ... )
            >>> tool_node = ToolNode(tools, awrap_tool_call=adapter.tool_output_wrapper(opts))

        Args:
            options: Optional :class:`MCPInterceptorOptions` controlling connector
                type derivation and operation type for input checks.
                Uses defaults if not provided.

        Returns:
            An async callable ``(call_request, execute) -> ToolMessage`` suitable
            for ``ToolNode(awrap_tool_call=...)``.

        Note:
            If you also use :meth:`mcp_tool_interceptor` on a
            ``MultiServerMCPClient``, MCP tool calls that flow through
            ``ToolNode`` will be policy-checked **twice** (once by the
            interceptor, once by this wrapper).  Use one or the other for
            MCP tools — this wrapper alone is sufficient when all tools
            (MCP and local) go through ``ToolNode``.
        """
        opts = options or MCPInterceptorOptions()

        def _default_connector_type(call_request: dict[str, Any]) -> str:
            name: str = call_request.get("name", "unknown_tool")
            return name

        resolve_connector_type = opts.connector_type_fn or _default_connector_type

        async def _wrapper(call_request: dict[str, Any], execute: Callable[..., Any]) -> Any:
            connector_type = resolve_connector_type(call_request)
            args = call_request.get("args", {})
            statement = json.dumps(args, default=str)

            input_check = await self.client.mcp_check_input(
                connector_type=connector_type,
                statement=statement,
                operation=opts.operation,
            )
            if not input_check.allowed:
                raise PolicyViolationError(
                    input_check.block_reason or "Tool call blocked by policy"
                )

            result = await execute(call_request)

            # Serialize content for output policy check
            content = getattr(result, "content", None)
            if content is None:
                return result

            if isinstance(content, str):
                serialized = content
            else:
                try:
                    serialized = json.dumps(content, default=str)
                except (TypeError, ValueError):
                    serialized = str(content)

            output_check = await self.client.mcp_check_output(
                connector_type=connector_type,
                message=serialized,
            )
            if not output_check.allowed:
                raise PolicyViolationError(
                    output_check.block_reason or "Tool output blocked by policy"
                )
            if output_check.redacted_data is not None:
                result.content = output_check.redacted_data
                return result

            return result

        return _wrapper

    async def __aenter__(self) -> AxonFlowLangGraphAdapter:
        """Context manager entry."""
        return self

    async def __aexit__(
        self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any
    ) -> None:
        """Context manager exit - abort if exception, complete otherwise."""
        if self.workflow_id:
            if exc_type is not None:
                await self.abort_workflow(f"Exception: {exc_val}")
            else:
                await self.complete_workflow()
