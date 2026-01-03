"""AxonFlow SDK Type Definitions.

All types are defined using Pydantic v2 for runtime validation
and automatic JSON serialization/deserialization.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Mode(str, Enum):
    """SDK operation mode."""

    PRODUCTION = "production"
    SANDBOX = "sandbox"


class RetryConfig(BaseModel):
    """Retry configuration with exponential backoff."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable retry logic")
    max_attempts: int = Field(default=3, ge=1, le=10, description="Max retry attempts")
    initial_delay: float = Field(default=1.0, gt=0, description="Initial delay (seconds)")
    max_delay: float = Field(default=30.0, gt=0, description="Max delay (seconds)")
    exponential_base: float = Field(default=2.0, gt=1, description="Backoff multiplier")


class CacheConfig(BaseModel):
    """Cache configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable caching")
    ttl: float = Field(default=60.0, gt=0, description="Cache TTL (seconds)")
    max_size: int = Field(default=1000, gt=0, description="Max cache entries")


class AxonFlowConfig(BaseModel):
    """Configuration for AxonFlow client.

    Attributes:
        agent_url: AxonFlow Agent URL (required)
        orchestrator_url: Orchestrator URL for Execution Replay API (optional, defaults to agent URL with port 8081)
        client_id: Client ID for authentication (optional for community/self-hosted mode)
        client_secret: Client secret for authentication (optional for community/self-hosted mode)
        license_key: Optional license key for organization-level auth
        mode: Operation mode (production or sandbox)
        debug: Enable debug logging
        timeout: Request timeout in seconds
        insecure_skip_verify: Skip TLS verification (dev only)
        retry: Retry configuration
        cache: Cache configuration

    Note:
        For community/self-hosted deployments, client_id and client_secret can be omitted.
        The SDK will work without authentication headers in this mode.
    """

    model_config = ConfigDict(frozen=True)

    agent_url: str = Field(..., min_length=1, description="AxonFlow Agent URL")
    orchestrator_url: str | None = Field(default=None, description="Orchestrator URL (for Execution Replay)")
    client_id: str | None = Field(default=None, description="Client ID (optional)")
    client_secret: str | None = Field(default=None, description="Client secret (optional)")
    license_key: str | None = Field(default=None, description="License key")
    mode: Mode = Field(default=Mode.PRODUCTION, description="Operation mode")
    debug: bool = Field(default=False, description="Enable debug logging")
    timeout: float = Field(default=60.0, gt=0, description="Request timeout (seconds)")
    map_timeout: float = Field(default=120.0, gt=0, description="MAP operations timeout (seconds)")
    insecure_skip_verify: bool = Field(default=False, description="Skip TLS verify")
    retry: RetryConfig = Field(default_factory=RetryConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


class ClientRequest(BaseModel):
    """Request to AxonFlow Agent."""

    query: str = Field(..., description="Query or prompt")
    user_token: str = Field(..., description="User token for auth")
    client_id: str | None = Field(default=None, description="Client ID (optional)")
    request_type: str = Field(..., description="Request type")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context")


class CodeArtifact(BaseModel):
    """Code artifact metadata detected in LLM responses.

    When an LLM generates code, AxonFlow automatically detects and analyzes it.
    This metadata is included in policy_info for audit and compliance.
    """

    is_code_output: bool = Field(default=False, description="Whether response contains code")
    language: str = Field(default="", description="Detected programming language")
    code_type: str = Field(default="", description="Code category (function, class, script, etc.)")
    size_bytes: int = Field(default=0, ge=0, description="Size of detected code in bytes")
    line_count: int = Field(default=0, ge=0, description="Number of lines of code")
    secrets_detected: int = Field(default=0, ge=0, description="Count of potential secrets found")
    unsafe_patterns: int = Field(default=0, ge=0, description="Count of unsafe code patterns")
    policies_checked: list[str] = Field(default_factory=list, description="Policies evaluated")


class PolicyEvaluationInfo(BaseModel):
    """Policy evaluation metadata."""

    policies_evaluated: list[str] = Field(default_factory=list)
    static_checks: list[str] = Field(default_factory=list)
    processing_time: str = Field(default="0ms")
    tenant_id: str = Field(default="")
    code_artifact: CodeArtifact | None = Field(default=None, description="Code metadata")


class ClientResponse(BaseModel):
    """Response from AxonFlow Agent."""

    success: bool = Field(..., description="Whether request succeeded")
    data: Any | None = Field(default=None, description="Response data")
    result: str | None = Field(default=None, description="Result for planning")
    plan_id: str | None = Field(default=None, description="Plan ID if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, description="Error message if failed")
    blocked: bool = Field(default=False, description="Whether request was blocked")
    block_reason: str | None = Field(default=None, description="Block reason")
    policy_info: PolicyEvaluationInfo | None = Field(default=None)


class ConnectorMetadata(BaseModel):
    """MCP connector metadata."""

    id: str
    name: str
    type: str
    version: str = ""
    description: str = ""
    category: str = ""
    icon: str = ""
    tags: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    installed: bool = False
    healthy: bool = False


class ConnectorInstallRequest(BaseModel):
    """Request to install an MCP connector."""

    connector_id: str
    name: str
    tenant_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class ConnectorResponse(BaseModel):
    """Response from MCP connector query."""

    success: bool
    data: Any | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """A step in a multi-agent plan."""

    id: str
    name: str
    type: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    agent: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class PlanResponse(BaseModel):
    """Multi-agent plan response."""

    plan_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    domain: str = "generic"
    complexity: int = 0
    parallel: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanExecutionResponse(BaseModel):
    """Plan execution result."""

    plan_id: str
    status: str  # "running", "completed", "failed"
    result: str | None = None
    step_results: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration: str | None = None


# Gateway Mode Types


class RateLimitInfo(BaseModel):
    """Rate limiting status."""

    limit: int
    remaining: int
    reset_at: datetime


class PolicyApprovalResult(BaseModel):
    """Pre-check result from Gateway Mode."""

    context_id: str = Field(..., description="Context ID for audit linking")
    approved: bool = Field(..., description="Whether request is approved")
    approved_data: dict[str, Any] = Field(default_factory=dict)
    policies: list[str] = Field(default_factory=list)
    rate_limit_info: RateLimitInfo | None = None
    expires_at: datetime
    block_reason: str | None = None


class TokenUsage(BaseModel):
    """LLM token usage tracking."""

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AuditResult(BaseModel):
    """Audit confirmation."""

    success: bool
    audit_id: str


# =========================================================================
# Execution Replay Types
# =========================================================================


class ExecutionSummary(BaseModel):
    """Workflow execution summary."""

    request_id: str = Field(..., description="Unique execution identifier")
    workflow_name: str = Field(default="", description="Name of the workflow")
    status: str = Field(..., description="Status: running, completed, failed")
    total_steps: int = Field(default=0, ge=0, description="Total number of steps")
    completed_steps: int = Field(default=0, ge=0, description="Completed steps")
    started_at: datetime = Field(..., description="When execution started")
    completed_at: datetime | None = Field(default=None, description="When execution completed")
    duration_ms: int | None = Field(default=None, ge=0, description="Duration in milliseconds")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens used")
    total_cost_usd: float = Field(default=0.0, ge=0, description="Total cost in USD")
    org_id: str = Field(default="", description="Organization ID")
    tenant_id: str = Field(default="", description="Tenant ID")
    user_id: str = Field(default="", description="User ID")
    error_message: str = Field(default="", description="Error message if failed")
    input_summary: Any | None = Field(default=None, description="Input summary")
    output_summary: Any | None = Field(default=None, description="Output summary")


class ExecutionSnapshot(BaseModel):
    """Snapshot of a workflow execution step."""

    request_id: str = Field(..., description="Execution identifier")
    step_index: int = Field(..., ge=0, description="Step position (0-indexed)")
    step_name: str = Field(..., description="Step name")
    status: str = Field(..., description="Step status")
    started_at: datetime = Field(..., description="Step start time")
    completed_at: datetime | None = Field(default=None, description="Step completion time")
    duration_ms: int | None = Field(default=None, ge=0, description="Duration in milliseconds")
    provider: str = Field(default="", description="LLM provider name")
    model: str = Field(default="", description="Model used")
    tokens_in: int = Field(default=0, ge=0, description="Input tokens")
    tokens_out: int = Field(default=0, ge=0, description="Output tokens")
    cost_usd: float = Field(default=0.0, ge=0, description="Step cost in USD")
    input: Any | None = Field(default=None, description="Step input")
    output: Any | None = Field(default=None, description="Step output")
    error_message: str = Field(default="", description="Error message if failed")
    policies_checked: list[str] = Field(default_factory=list, description="Policies evaluated")
    policies_triggered: list[str] = Field(default_factory=list, description="Policies triggered")
    approval_required: bool = Field(default=False, description="Whether approval was required")
    approved_by: str = Field(default="", description="Approver ID")
    approved_at: str = Field(default="", description="Approval timestamp")


class TimelineEntry(BaseModel):
    """Timeline entry for execution visualization."""

    step_index: int = Field(..., ge=0, description="Step position")
    step_name: str = Field(..., description="Step name")
    status: str = Field(..., description="Step status")
    started_at: datetime = Field(..., description="Step start time")
    completed_at: datetime | None = Field(default=None, description="Step completion time")
    duration_ms: int | None = Field(default=None, ge=0, description="Duration in milliseconds")
    has_error: bool = Field(default=False, description="Whether step has error")
    has_approval: bool = Field(default=False, description="Whether step required approval")


class ListExecutionsResponse(BaseModel):
    """Response from list executions API."""

    executions: list[ExecutionSummary] = Field(default_factory=list)
    total: int = Field(default=0, ge=0, description="Total count")
    limit: int = Field(default=50, ge=1, description="Page size")
    offset: int = Field(default=0, ge=0, description="Offset")


class ExecutionDetail(BaseModel):
    """Full execution with summary and steps."""

    summary: ExecutionSummary
    steps: list[ExecutionSnapshot] = Field(default_factory=list)


class ListExecutionsOptions(BaseModel):
    """Options for listing executions."""

    limit: int = Field(default=50, ge=1, le=100, description="Page size")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    status: str | None = Field(default=None, description="Filter by status")
    workflow_id: str | None = Field(default=None, description="Filter by workflow")
    start_time: datetime | None = Field(default=None, description="Filter from timestamp")
    end_time: datetime | None = Field(default=None, description="Filter to timestamp")


class ExecutionExportOptions(BaseModel):
    """Options for exporting an execution."""

    format: str = Field(default="json", description="Export format")
    include_input: bool = Field(default=True, description="Include step inputs")
    include_output: bool = Field(default=True, description="Include step outputs")
    include_policies: bool = Field(default=True, description="Include policy details")
