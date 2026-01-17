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

from typing import TYPE_CHECKING, Any

from axonflow.workflow import (
    CreateWorkflowRequest,
    GateDecision,
    MarkStepCompletedRequest,
    StepGateRequest,
    StepType,
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
        >>> await adapter.start_workflow(total_steps=5)
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
        total_steps: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Register the workflow with AxonFlow.

        Call this at the start of your LangGraph workflow execution.

        Args:
            total_steps: Total number of steps (if known)
            metadata: Additional workflow metadata

        Returns:
            The assigned workflow ID

        Example:
            >>> workflow_id = await adapter.start_workflow(
            ...     total_steps=5,
            ...     metadata={"customer_id": "cust-123"}
            ... )
        """
        request = CreateWorkflowRequest(
            workflow_name=self.workflow_name,
            source=self.source,
            total_steps=total_steps,
            metadata=metadata or {},
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
            raise ValueError("Workflow not started. Call start_workflow() first.")

        # Convert string to StepType if needed
        if isinstance(step_type, str):
            step_type = StepType(step_type)

        # Generate step ID if not provided
        if step_id is None:
            self._step_counter += 1
            step_id = f"step-{self._step_counter}-{step_name.lower().replace(' ', '-')}"

        request = StepGateRequest(
            step_name=step_name,
            step_type=step_type,
            step_input=step_input or {},
            model=model,
            provider=provider,
        )

        response = await self.client.step_gate(self.workflow_id, step_id, request)

        if response.decision == GateDecision.BLOCK:
            if self._auto_block:
                raise WorkflowBlockedError(
                    f"Step '{step_name}' blocked: {response.reason}",
                    step_id=response.step_id,
                    reason=response.reason,
                    policy_ids=response.policy_ids,
                )
            return False

        if response.decision == GateDecision.REQUIRE_APPROVAL:
            raise WorkflowApprovalRequiredError(
                f"Step '{step_name}' requires approval",
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
    ) -> None:
        """Mark a step as completed.

        Call this after successfully executing a LangGraph node.

        Args:
            step_name: Step name (used to generate step_id if not provided)
            step_id: Optional step ID (must match the one used in check_gate)
            output: Output data from the step
            metadata: Additional metadata

        Example:
            >>> await adapter.step_completed("generate", output={"code": result})
        """
        if not self.workflow_id:
            raise ValueError("Workflow not started. Call start_workflow() first.")

        # Generate step ID if not provided (must match check_gate)
        if step_id is None:
            step_id = f"step-{self._step_counter}-{step_name.lower().replace(' ', '-')}"

        request = MarkStepCompletedRequest(
            output=output or {},
            metadata=metadata or {},
        )

        await self.client.mark_step_completed(self.workflow_id, step_id, request)

    async def complete_workflow(self) -> None:
        """Mark the workflow as completed.

        Call this when your LangGraph workflow finishes successfully.

        Example:
            >>> await adapter.complete_workflow()
        """
        if not self.workflow_id:
            raise ValueError("Workflow not started. Call start_workflow() first.")

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
            raise ValueError("Workflow not started. Call start_workflow() first.")

        await self.client.abort_workflow(self.workflow_id, reason)

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
        import asyncio

        if not self.workflow_id:
            raise ValueError("Workflow not started. Call start_workflow() first.")

        elapsed = 0.0
        while elapsed < timeout:
            status = await self.client.get_workflow(self.workflow_id)

            # Find the step
            for step in status.steps:
                if step.step_id == step_id:
                    if step.approval_status:
                        from axonflow.workflow import ApprovalStatus

                        if step.approval_status == ApprovalStatus.APPROVED:
                            return True
                        if step.approval_status == ApprovalStatus.REJECTED:
                            return False
                    break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"Approval timeout after {timeout}s for step {step_id}")

    async def __aenter__(self) -> AxonFlowLangGraphAdapter:
        """Context manager entry."""
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any) -> None:
        """Context manager exit - abort if exception, complete otherwise."""
        if self.workflow_id:
            if exc_type is not None:
                await self.abort_workflow(f"Exception: {exc_val}")
            else:
                await self.complete_workflow()
