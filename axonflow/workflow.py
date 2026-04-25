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
    """Response from creating a workflow.

    Wire shape: ``{workflow_id, workflow_name, status, started_at, trace_id}``.
    The legacy ``source`` and ``created_at`` fields are kept for source-compat
    only — the wire emits ``started_at`` (not ``created_at``) and does not
    include ``source`` on the create response.
    """

    workflow_id: str = Field(..., description="Unique identifier for the workflow")
    workflow_name: str = Field(..., description="Name of the workflow")
    status: WorkflowStatus = Field(..., description="Current status (always 'in_progress' for new)")
    started_at: datetime | None = Field(
        default=None,
        description="When the workflow started executing (canonical wire field).",
    )
    trace_id: str | None = None
    created_at: datetime | None = Field(
        default=None,
        description=(
            "DEPRECATED: the wire emits `started_at`, not `created_at`. "
            "Always read None against JSON-decoded responses. Use `started_at`. "
            "Removed in v7."
        ),
    )
    source: WorkflowSource | None = Field(
        default=None,
        description=(
            "DEPRECATED: not emitted on the create response. Read `source` from a "
            "subsequent `get_workflow()` call instead. Removed in v7."
        ),
    )


class ToolContext(BaseModel):
    """Tool-level context for per-tool governance within tool_call steps."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    tool_type: str | None = Field(default=None, description="Tool type: function, mcp, api")
    tool_input: dict[str, Any] = Field(default_factory=dict)


class RetryPolicy(str, Enum):
    """Controls step gate retry behavior for the same (workflow_id, step_id)."""

    IDEMPOTENT = "idempotent"
    """Return cached decision if the step was already evaluated (default)."""
    REEVALUATE = "reevaluate"
    """Force fresh policy evaluation regardless of prior decision."""


class PriorCompletionStatus(str, Enum):
    """State of the prior gate+complete cycle for a step."""

    NONE = "none"
    """First gate call, no prior gates on this step."""
    COMPLETED = "completed"
    """A prior gate call and a prior /complete both landed for this (workflow_id, step_id)."""
    GATED_NOT_COMPLETED = "gated_not_completed"
    """A prior gate landed but no /complete has followed for this (workflow_id, step_id)."""


class RetryContext(BaseModel):
    """First-class state signal returned on every step gate response.

    Replaces the ambiguous ``cached: bool`` field. Callers should migrate off
    ``cached`` and ``decision_source`` to the richer fields here.
    """

    gate_count: int = Field(
        ...,
        ge=1,
        description=(
            "Number of /gate calls for this (workflow_id, step_id), including the current call."
        ),
    )
    completion_count: int = Field(
        ...,
        ge=0,
        description="Number of successful /complete calls for this (workflow_id, step_id).",
    )
    prior_completion_status: PriorCompletionStatus = Field(
        ..., description="Whether a prior gate+complete cycle has landed."
    )
    prior_output_available: bool = Field(
        ...,
        description='True iff prior_completion_status == "completed".',
    )
    prior_output: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Output from the prior /complete, or None. Non-None only when the gate call was made "
            "with include_prior_output=True AND prior_completion_status == 'completed'."
        ),
    )
    prior_completion_at: datetime | None = Field(
        default=None,
        description="Timestamp of the prior /complete, if any.",
    )
    first_attempt_at: datetime = Field(
        ...,
        description="Timestamp of the first gate call for this (workflow_id, step_id).",
    )
    last_attempt_at: datetime = Field(
        ...,
        description="Timestamp of the current gate call.",
    )
    last_decision: GateDecision = Field(
        ...,
        description=(
            "Decision of the immediately prior gate call. On first call, equals the current "
            "call's decision."
        ),
    )
    idempotency_key: str = Field(
        default="",
        description=(
            "Key the caller set on this step (from the first gate call that supplied one), or "
            'empty string "" if the caller never supplied one. Always present — never None — '
            "per the wire contract (WCP_RETRY_IDEMPOTENCY_WIRE_CONTRACT.md §3). Immutable once set."
        ),
    )


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
    retry_policy: RetryPolicy | None = Field(
        default=None,
        description='Retry behavior: "idempotent" (default) returns cached decision, '
        '"reevaluate" forces fresh evaluation',
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Caller-supplied opaque business-level key. Once set on the first gate call for a "
            "(workflow_id, step_id), it is immutable — subsequent gate/complete calls must pass "
            "the same key or raise IdempotencyKeyMismatchError. Echoed on "
            "retry_context.idempotency_key."
        ),
    )
    tokens_in: int | None = Field(
        default=None, description="Estimated input tokens for this step (used by budget policies)."
    )
    tokens_out: int | None = Field(
        default=None, description="Estimated output tokens for this step (used by budget policies)."
    )
    cost_usd: float | None = Field(
        default=None, description="Estimated cost in USD for this step (used by budget policies)."
    )


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
    decision_id: str | None = Field(
        default=None,
        description=(
            "Unique decision identifier for auditing (links a gate response to its audit row)."
        ),
    )
    policies_evaluated: list[PolicyMatch] | None = Field(
        default=None,
        description="List of all policies that were evaluated during the gate check (Issue #1019)",
    )
    policies_matched: list[PolicyMatch] | None = Field(
        default=None,
        description="List of policies that matched and influenced the decision (Issue #1019)",
    )
    cached: bool = Field(
        default=False,
        description=(
            "[DEPRECATED] Use retry_context.gate_count > 1 instead. "
            "Will be removed in a future major version."
        ),
    )
    decision_source: str | None = Field(
        default=None,
        description=(
            "[DEPRECATED] Use retry_context.prior_completion_status instead. "
            "Will be removed in a future major version."
        ),
    )
    retry_context: RetryContext | None = Field(
        default=None,
        description=(
            "First-class state signal for (workflow_id, step_id). Always present on every gate "
            "response from platform v7.3.0+. Nullable in the SDK model only so older platform "
            "responses that omit it still parse; expect it populated in practice."
        ),
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


class Checkpoint(BaseModel):
    """A governance-aware resume boundary at a step-gate evaluation."""

    id: int = Field(..., description="Database identifier")
    workflow_id: str = Field(..., description="Workflow this checkpoint belongs to")
    step_id: str = Field(..., description="Step this checkpoint was created at")
    step_index: int = Field(..., description="Position of the step in the workflow")
    step_type: str | None = Field(default=None, description="Type of step")
    checkpoint_type: str = Field(..., description="Classification: step_gate or approval_boundary")
    gate_decision: str = Field(..., description="Decision at this checkpoint")
    gate_reason: str | None = Field(default=None, description="Reason for the decision")
    is_resumable: bool = Field(
        default=True, description="Whether the workflow can resume from here"
    )
    resume_count: int = Field(default=0, description="How many times resumed from this checkpoint")
    created_at: str = Field(..., description="When the checkpoint was created")


class CheckpointListResponse(BaseModel):
    """Response from listing checkpoints."""

    checkpoints: list[Checkpoint] = Field(default_factory=list)
    workflow_id: str = Field(...)


class ResumeFromCheckpointResponse(BaseModel):
    """Response after resuming from a checkpoint."""

    workflow_id: str = Field(...)
    resumed_from_checkpoint: str = Field(..., description="step_id of the checkpoint")
    resumed_from_index: int = Field(...)
    new_decision: str = Field(...)
    decision_source: str = Field(default="fresh")
    resume_count: int = Field(...)
    message: str = Field(...)


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
    metadata: dict[str, object] | None = Field(
        default=None, description="Arbitrary workflow metadata, opaque to the platform."
    )

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
    limit: int | None = Field(
        default=None, description="Echo of the limit query parameter (for pagination clients)."
    )
    offset: int | None = Field(
        default=None, description="Echo of the offset query parameter (for pagination clients)."
    )


class MarkStepCompletedRequest(BaseModel):
    """Request to mark a step as completed."""

    model_config = ConfigDict(frozen=True)

    output: dict[str, Any] = Field(default_factory=dict, description="Output data from the step")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    tokens_in: int | None = Field(default=None, ge=0, description="Input tokens consumed")
    tokens_out: int | None = Field(default=None, ge=0, description="Output tokens produced")
    cost_usd: float | None = Field(default=None, ge=0, description="Cost in USD")
    idempotency_key: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Must match the key passed on the corresponding gate call, if any. Mismatch "
            "(including missing-vs-set on either side) raises IdempotencyKeyMismatchError."
        ),
    )


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
    """Response from approving a workflow step.

    Starting with v6.6.0 the server returns the rich step-gate shape: ``decision``
    resolves to ``"allow"`` once approved, ``retry_context`` mirrors the gate
    response retry state, ``approved_by`` / ``approved_at`` carry the reviewer
    identity, ``approval_id`` is the deterministic HITL queue entry UUID, and
    ``policies_matched`` reconstructs the governance trail. The legacy
    ``workflow_id`` / ``step_id`` / ``status`` fields remain for back-compat.

    See ADR-046 (HITL response parity) — the same shape is returned by both the
    WCP endpoint and the MAP plan-scoped equivalent.
    """

    model_config = ConfigDict(extra="allow")

    workflow_id: str = Field(..., description="Workflow ID")
    plan_id: str | None = Field(
        default=None,
        description="MAP plan ID — populated on plan-scoped responses",
    )
    step_id: str = Field(..., description="Step ID that was approved")
    status: str | None = Field(
        default=None, description="Legacy status field (mirrors approval_status)"
    )
    decision: str | None = Field(
        default=None, description="Post-approval decision (allow / block / require_approval)"
    )
    reason: str | None = Field(default=None, description="Decision reason text")
    approval_status: str | None = Field(default=None, description="pending / approved / rejected")
    approval_id: str | None = Field(default=None, description="Deterministic HITL queue UUID")
    approved_by: str | None = Field(default=None, description="Identity that approved the step")
    approved_at: str | None = Field(
        default=None, description="ISO 8601 timestamp when the approval was persisted"
    )
    policies_matched: list[PolicyMatch] | None = Field(
        default=None, description="Policies that triggered the require_approval decision"
    )
    retry_context: RetryContext | None = Field(
        default=None,
        description="Retry / idempotency state — mirrors gate response",
    )
    message: str | None = Field(default=None, description="Human-readable summary")


class RejectStepResponse(BaseModel):
    """Response from rejecting a workflow step.

    Symmetric with :class:`ApproveStepResponse` — ``decision`` resolves to
    ``"block"``, ``rejected_by`` / ``rejected_at`` populate instead of
    approved_*. See ADR-046.
    """

    model_config = ConfigDict(extra="allow")

    workflow_id: str = Field(..., description="Workflow ID")
    plan_id: str | None = Field(default=None, description="MAP plan ID")
    step_id: str = Field(..., description="Step ID that was rejected")
    status: str | None = Field(
        default=None, description="Legacy status field (mirrors approval_status)"
    )
    decision: str | None = Field(default=None, description="Post-rejection decision (block)")
    reason: str | None = Field(default=None, description="Decision reason text")
    approval_status: str | None = Field(default=None)
    approval_id: str | None = Field(default=None, description="Deterministic HITL queue UUID")
    rejected_by: str | None = Field(default=None, description="Identity that rejected the step")
    rejected_at: str | None = Field(default=None, description="ISO 8601 rejection timestamp")
    policies_matched: list[PolicyMatch] | None = Field(default=None)
    retry_context: RetryContext | None = Field(
        default=None, description="Retry / idempotency state"
    )
    message: str | None = Field(default=None)


class PendingApproval(BaseModel):
    """A pending approval for a workflow step.

    Populated by both ``get_pending_approvals`` (WCP plane) and
    ``get_pending_plan_approvals`` (MAP plane). The ``plan_id`` field is the
    one intentional asymmetry between the two planes — populated on MAP-plane
    entries, ``None`` on WCP-plane entries (mirrors ADR-046 parity rule).
    """

    workflow_id: str = Field(..., description="Workflow ID")
    workflow_name: str = Field(..., description="Workflow name")
    plan_id: str | None = Field(
        default=None,
        description=("MAP plan id — populated on MAP-plane entries; None on WCP-plane entries."),
    )
    step_id: str = Field(..., description="Step ID awaiting approval")
    step_index: int = Field(default=0, description="Zero-based step index within the workflow")
    step_name: str | None = Field(default=None, description="Step name")
    step_type: str | None = Field(default=None, description="Step type")
    decision: str = Field(
        default="require_approval",
        description=(
            "Gate decision that paused the step — always require_approval for pending entries"
        ),
    )
    decision_reason: str | None = Field(default=None, description="Why the step was paused")
    policies_matched: list[dict[str, Any]] | None = Field(
        default=None, description="Policies that triggered the approval requirement"
    )
    step_input: dict[str, Any] | None = Field(
        default=None, description="Step input payload (may be redacted)"
    )
    approval_status: str | None = Field(
        default=None,
        description="Current approval state — pending for listed entries",
    )
    created_at: str = Field(..., description="When the approval was requested")


class PendingApprovalsResponse(BaseModel):
    """Response containing pending approvals.

    Shape matches the server wire contract: ``pending_approvals`` array +
    ``count``.
    """

    pending_approvals: list[PendingApproval] = Field(
        default_factory=list, description="List of pending approvals"
    )
    count: int = Field(
        default=0,
        ge=0,
        description="Total count of pending approvals matching the scope",
    )
