"""Unified Execution Tracking Types for AxonFlow SDK.

These types provide a consistent interface for tracking both Multi-Agent Planning (MAP)
and Workflow Control Plane (WCP) executions. The unified schema enables consistent
status tracking, progress reporting, and cost tracking across execution types.

Issue #1075 - EPIC #1074: Unified Workflow Infrastructure
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionType(str, Enum):
    """Execution type distinguishing between MAP plans and WCP workflows."""

    MAP_PLAN = "map_plan"
    WCP_WORKFLOW = "wcp_workflow"


class ExecutionStatusValue(str, Enum):
    """Unified execution status values."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABORTED = "aborted"  # WCP-specific: workflow aborted
    EXPIRED = "expired"  # MAP-specific: plan expired before execution

    def is_terminal(self) -> bool:
        """Check if the execution status is terminal (no more updates expected)."""
        return self in (
            ExecutionStatusValue.COMPLETED,
            ExecutionStatusValue.FAILED,
            ExecutionStatusValue.CANCELLED,
            ExecutionStatusValue.ABORTED,
            ExecutionStatusValue.EXPIRED,
        )


class StepStatusValue(str, Enum):
    """Step status values."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"  # WCP: blocked by policy
    APPROVAL = "approval"  # WCP: waiting for approval

    def is_terminal(self) -> bool:
        """Check if the step status is terminal."""
        return self in (
            StepStatusValue.COMPLETED,
            StepStatusValue.FAILED,
            StepStatusValue.SKIPPED,
        )

    def is_blocking(self) -> bool:
        """Check if the step is in a blocking state."""
        return self in (StepStatusValue.BLOCKED, StepStatusValue.APPROVAL)


class UnifiedStepType(str, Enum):
    """Step type indicating what kind of operation the step performs."""

    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    CONNECTOR_CALL = "connector_call"
    HUMAN_TASK = "human_task"
    SYNTHESIS = "synthesis"  # MAP: result synthesis step
    ACTION = "action"  # Generic action step
    GATE = "gate"  # WCP: policy gate evaluation


class UnifiedGateDecision(str, Enum):
    """Gate decision values (applicable to both MAP and WCP)."""

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


class UnifiedApprovalStatus(str, Enum):
    """Approval status for require_approval decisions."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class UnifiedStepStatus(BaseModel):
    """Detailed information about an individual execution step."""

    step_id: str = Field(..., description="Unique step identifier")
    step_index: int = Field(..., ge=0, description="Step index in the execution (0-based)")
    step_name: str = Field(..., description="Human-readable step name")
    step_type: UnifiedStepType = Field(..., description="Type of operation the step performs")
    status: StepStatusValue = Field(..., description="Current status of the step")
    started_at: datetime | None = Field(default=None, description="When the step started executing")
    ended_at: datetime | None = Field(default=None, description="When the step finished")
    duration: str | None = Field(
        default=None, description="Duration of step execution (human-readable)"
    )
    decision: UnifiedGateDecision | None = Field(
        default=None, description="Policy decision for this step"
    )
    decision_reason: str | None = Field(default=None, description="Reason for the policy decision")
    policies_matched: list[str] = Field(
        default_factory=list, description="IDs of policies that matched during evaluation"
    )
    approval_status: UnifiedApprovalStatus | None = Field(
        default=None, description="Approval status (for require_approval decisions)"
    )
    approved_by: str | None = Field(default=None, description="Who approved the step")
    approved_at: datetime | None = Field(default=None, description="When the step was approved")
    model: str | None = Field(default=None, description="LLM model used")
    provider: str | None = Field(default=None, description="LLM provider")
    cost_usd: float | None = Field(default=None, description="Cost in USD for this step")
    input: Any | None = Field(default=None, description="Step input data")
    output: Any | None = Field(default=None, description="Step output data")
    result_summary: str | None = Field(default=None, description="Human-readable result summary")
    error: str | None = Field(default=None, description="Error message if step failed")


class ExecutionStatus(BaseModel):
    """Unified execution status for both MAP plans and WCP workflows.

    This type provides a consistent interface for tracking execution progress,
    steps, costs, and metadata regardless of the underlying execution type.

    Example:
        >>> # Get execution status
        >>> status = await client.get_execution_status('exec_123')
        >>> print(f"{status.execution_type}: {status.name}")
        >>> print(f"Progress: {status.progress_percent}%")
        >>>
        >>> # Check if complete
        >>> if status.status == ExecutionStatusValue.COMPLETED:
        ...     print(f"Completed in {status.duration}")
        >>>
        >>> # Access steps
        >>> for step in status.steps:
        ...     print(f"Step {step.step_index}: {step.step_name} - {step.status}")
    """

    execution_id: str = Field(..., description="Unique execution identifier")
    execution_type: ExecutionType = Field(
        ..., description="Type of execution (MAP plan or WCP workflow)"
    )
    name: str = Field(..., description="Human-readable name of the execution")
    source: str | None = Field(
        default=None, description="Source orchestrator (WCP-specific: langchain, crewai, etc.)"
    )
    status: ExecutionStatusValue = Field(..., description="Current execution status")
    current_step_index: int = Field(
        default=0, ge=0, description="Current step being executed (0-based index)"
    )
    total_steps: int = Field(default=0, ge=0, description="Total number of steps in the execution")
    progress_percent: float = Field(
        default=0.0, ge=0, le=100, description="Progress as a percentage (0-100)"
    )
    started_at: datetime = Field(..., description="When execution started")
    completed_at: datetime | None = Field(default=None, description="When execution completed")
    duration: str | None = Field(default=None, description="Duration of execution (human-readable)")
    estimated_cost_usd: float | None = Field(
        default=None, description="Estimated cost in USD (pre-execution)"
    )
    actual_cost_usd: float | None = Field(
        default=None, description="Actual cost in USD (post-execution)"
    )
    steps: list[UnifiedStepStatus] = Field(
        default_factory=list, description="Detailed step information"
    )
    error: str | None = Field(default=None, description="Error message if execution failed")
    tenant_id: str | None = Field(default=None, description="Tenant ID for multi-tenancy")
    org_id: str | None = Field(default=None, description="Organization ID")
    user_id: str | None = Field(default=None, description="User ID who initiated the execution")
    client_id: str | None = Field(default=None, description="Client/application ID")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_at: datetime = Field(..., description="When the execution record was created")
    updated_at: datetime = Field(..., description="When the execution record was last updated")

    def is_terminal(self) -> bool:
        """Check if the execution is in a terminal state."""
        return self.status.is_terminal()

    def get_current_step(self) -> UnifiedStepStatus | None:
        """Get the currently running step, if any."""
        for step in self.steps:
            if step.status == StepStatusValue.RUNNING:
                return step
        return None

    def calculate_total_cost(self) -> float:
        """Calculate total cost from all steps."""
        return sum(step.cost_usd or 0.0 for step in self.steps)

    def is_map_plan(self) -> bool:
        """Check if this is a MAP plan execution."""
        return self.execution_type == ExecutionType.MAP_PLAN

    def is_wcp_workflow(self) -> bool:
        """Check if this is a WCP workflow execution."""
        return self.execution_type == ExecutionType.WCP_WORKFLOW


class UnifiedListExecutionsRequest(BaseModel):
    """Request to list executions with optional filters."""

    execution_type: ExecutionType | None = Field(
        default=None, description="Filter by execution type"
    )
    status: ExecutionStatusValue | None = Field(default=None, description="Filter by status")
    tenant_id: str | None = Field(default=None, description="Filter by tenant ID")
    org_id: str | None = Field(default=None, description="Filter by organization ID")
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of results")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


class UnifiedListExecutionsResponse(BaseModel):
    """Paginated response for listing executions."""

    executions: list[ExecutionStatus] = Field(
        default_factory=list, description="List of executions"
    )
    total: int = Field(default=0, ge=0, description="Total count of matching executions")
    limit: int = Field(default=50, description="Limit used in the request")
    offset: int = Field(default=0, description="Offset used in the request")
    has_more: bool = Field(default=False, description="Whether more results are available")
