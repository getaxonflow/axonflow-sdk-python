"""Workflow Control Plane Types for AxonFlow SDK.

The Workflow Control Plane provides governance gates for external orchestrators
like LangChain, LangGraph, and CrewAI. These types define the request/response
structures for registering workflows, checking step gates, and managing workflow
lifecycle.

"LangChain runs the workflow. AxonFlow decides when it's allowed to move forward."
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(str, Enum):
    """Workflow status values."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """Check if the workflow status is terminal (completed, aborted, or failed)."""
        return self in (WorkflowStatus.COMPLETED, WorkflowStatus.ABORTED, WorkflowStatus.FAILED)


class WorkflowSource(str, Enum):
    """Source of the workflow (which orchestrator is running it)."""

    LANGGRAPH = "langgraph"
    LANGCHAIN = "langchain"
    CREWAI = "crewai"
    EXTERNAL = "external"


class GateDecision(str, Enum):
    """Gate decision values returned by step gate checks."""

    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


class ApprovalStatus(str, Enum):
    """Approval status for steps requiring human approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class StepType(str, Enum):
    """Step type indicating what kind of operation the step performs."""

    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    CONNECTOR_CALL = "connector_call"
    HUMAN_TASK = "human_task"


class CreateWorkflowRequest(BaseModel):
    """Request to create a new workflow."""

    model_config = ConfigDict(frozen=True)

    workflow_name: str = Field(
        ..., min_length=1, description="Human-readable name for the workflow"
    )
    source: WorkflowSource | None = Field(
        default=None, description="Source orchestrator running the workflow"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata for the workflow"
    )
    trace_id: str | None = Field(
        default=None,
        description="External trace ID for correlation (Langsmith, Datadog, OTel)",
    )


class CreateWorkflowResponse(BaseModel):
    """Response from creating a workflow."""

    workflow_id: str = Field(..., description="Unique identifier for the workflow")
    workflow_name: str = Field(..., description="Name of the workflow")
    source: WorkflowSource = Field(..., description="Source orchestrator")
    status: WorkflowStatus = Field(..., description="Current status (always 'in_progress' for new)")
    created_at: datetime = Field(..., description="When the workflow was created")
    trace_id: str | None = None


class ToolContext(BaseModel):
    """Tool-level context for per-tool governance within tool_call steps."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    tool_type: str | None = Field(default=None, description="Tool type: function, mcp, api")
    tool_input: dict[str, Any] = Field(default_factory=dict)


class StepGateRequest(BaseModel):
    """Request to check if a step is allowed to proceed."""

    model_config = ConfigDict(frozen=True)

    step_name: str | None = Field(default=None, description="Human-readable name for the step")
    step_type: StepType = Field(..., description="Type of step being executed")
    step_input: dict[str, Any] = Field(
        default_factory=dict, description="Input data for the step (for policy evaluation)"
    )
    model: str | None = Field(default=None, description="LLM model being used (if applicable)")
    provider: str | None = Field(default=None, description="LLM provider (if applicable)")
    tool_context: ToolContext | None = None


class StepGateResponse(BaseModel):
    """Response from a step gate check."""

    decision: GateDecision = Field(
        ..., description="The gate decision: allow, block, or require_approval"
    )
    step_id: str = Field(..., description="Unique step ID assigned by the system")
    reason: str | None = Field(
        default=None, description="Reason for the decision (especially for block/require_approval)"
    )
    policy_ids: list[str] = Field(
        default_factory=list, description="IDs of policies that matched and influenced the decision"
    )
    approval_url: str | None = Field(
        default=None, description="URL to the approval portal (if decision is require_approval)"
    )
    policies_evaluated: list[PolicyMatch] | None = Field(
        default=None,
        description="List of all policies that were evaluated during the gate check (Issue #1019)",
    )
    policies_matched: list[PolicyMatch] | None = Field(
        default=None,
        description="List of policies that matched and influenced the decision (Issue #1019)",
    )

    def is_allowed(self) -> bool:
        """Check if the step is allowed to proceed."""
        return self.decision == GateDecision.ALLOW

    def is_blocked(self) -> bool:
        """Check if the step is blocked by policy."""
        return self.decision == GateDecision.BLOCK

    def requires_approval(self) -> bool:
        """Check if the step requires human approval."""
        return self.decision == GateDecision.REQUIRE_APPROVAL


class WorkflowStepInfo(BaseModel):
    """Information about a workflow step."""

    step_id: str = Field(..., description="Unique step identifier")
    step_index: int = Field(..., ge=0, description="Step index in the workflow")
    step_name: str | None = Field(default=None, description="Step name")
    step_type: StepType = Field(..., description="Step type")
    decision: GateDecision = Field(..., description="Gate decision for this step")
    decision_reason: str | None = Field(default=None, description="Reason for the decision")
    approval_status: ApprovalStatus | None = Field(
        default=None, description="Approval status (if require_approval decision)"
    )
    approved_by: str | None = Field(default=None, description="Who approved the step (if approved)")
    gate_checked_at: datetime = Field(..., description="When the gate was checked")
    completed_at: datetime | None = Field(default=None, description="When the step was completed")


class WorkflowStatusResponse(BaseModel):
    """Response containing workflow status."""

    workflow_id: str = Field(..., description="Workflow ID")
    workflow_name: str = Field(..., description="Workflow name")
    source: WorkflowSource = Field(..., description="Source orchestrator")
    status: WorkflowStatus = Field(..., description="Current status")
    current_step_index: int = Field(default=0, ge=0, description="Current step index (0-based)")
    total_steps: int | None = Field(default=None, ge=0, description="Total steps in the workflow")
    started_at: datetime = Field(..., description="When the workflow started")
    completed_at: datetime | None = Field(
        default=None, description="When the workflow completed (if completed)"
    )
    steps: list[WorkflowStepInfo] = Field(
        default_factory=list, description="List of steps in the workflow"
    )
    trace_id: str | None = None

    def is_terminal(self) -> bool:
        """Check if the workflow is in a terminal state (completed, aborted, or failed)."""
        return self.status.is_terminal()


class ListWorkflowsOptions(BaseModel):
    """Options for listing workflows."""

    model_config = ConfigDict(frozen=True)

    status: WorkflowStatus | None = Field(default=None, description="Filter by workflow status")
    source: WorkflowSource | None = Field(default=None, description="Filter by source")
    trace_id: str | None = Field(default=None, description="Filter by external trace ID")
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


class ListWorkflowsResponse(BaseModel):
    """Response from listing workflows."""

    workflows: list[WorkflowStatusResponse] = Field(
        default_factory=list, description="List of workflows"
    )
    total: int = Field(default=0, ge=0, description="Total count (for pagination)")


class MarkStepCompletedRequest(BaseModel):
    """Request to mark a step as completed."""

    model_config = ConfigDict(frozen=True)

    output: dict[str, Any] = Field(default_factory=dict, description="Output data from the step")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    tokens_in: int | None = Field(default=None, ge=0, description="Input tokens consumed")
    tokens_out: int | None = Field(default=None, ge=0, description="Output tokens produced")
    cost_usd: float | None = Field(default=None, ge=0, description="Cost in USD")


class AbortWorkflowRequest(BaseModel):
    """Request to abort a workflow."""

    model_config = ConfigDict(frozen=True)

    reason: str | None = Field(default=None, description="Reason for aborting the workflow")


class PolicyMatch(BaseModel):
    """Policy match information."""

    policy_id: str = Field(..., description="Policy ID that matched")
    policy_name: str = Field(..., description="Policy name")
    action: str = Field(..., description="Action taken by the policy")
    reason: str | None = Field(default=None, description="Reason for the match")


# =========================================================================
# WCP Approval Types (Feature 5)
# =========================================================================


class ApproveStepResponse(BaseModel):
    """Response from approving a workflow step."""

    workflow_id: str = Field(..., description="Workflow ID")
    step_id: str = Field(..., description="Step ID that was approved")
    status: str = Field(..., description="Approval status")


class RejectStepResponse(BaseModel):
    """Response from rejecting a workflow step."""

    workflow_id: str = Field(..., description="Workflow ID")
    step_id: str = Field(..., description="Step ID that was rejected")
    status: str = Field(..., description="Rejection status")


class PendingApproval(BaseModel):
    """A pending approval for a workflow step."""

    workflow_id: str = Field(..., description="Workflow ID")
    workflow_name: str = Field(..., description="Workflow name")
    step_id: str = Field(..., description="Step ID awaiting approval")
    step_name: str = Field(..., description="Step name")
    step_type: str = Field(..., description="Step type")
    created_at: str = Field(..., description="When the approval was requested")


class PendingApprovalsResponse(BaseModel):
    """Response containing pending approvals."""

    approvals: list[PendingApproval] = Field(
        default_factory=list, description="List of pending approvals"
    )
    total: int = Field(default=0, ge=0, description="Total count of pending approvals")
