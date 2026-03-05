"""AxonFlow SDK Type Definitions.

All types are defined using Pydantic v2 for runtime validation
and automatic JSON serialization/deserialization.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
        endpoint: AxonFlow endpoint URL (required) - single entry point for all services
        client_id: Client ID for authentication (optional for community/self-hosted mode)
        client_secret: Client secret for authentication (optional for community/self-hosted mode)
        mode: Operation mode (production or sandbox)
        debug: Enable debug logging
        timeout: Request timeout in seconds
        insecure_skip_verify: Skip TLS verification (dev only)
        retry: Retry configuration
        cache: Cache configuration

    Note:
        For community/self-hosted deployments, client_id and client_secret can be omitted.
        The SDK will work without authentication headers in this mode.

        As of v1.0.0, all routes go through a single endpoint (ADR-026).
    """

    model_config = ConfigDict(frozen=True)

    endpoint: str = Field(..., min_length=1, description="AxonFlow endpoint URL")
    client_id: str | None = Field(default=None, description="Client ID (optional)")
    client_secret: str | None = Field(default=None, description="Client secret (optional)")
    mode: Mode = Field(default=Mode.PRODUCTION, description="Operation mode")
    debug: bool = Field(default=False, description="Enable debug logging")
    timeout: float = Field(default=60.0, gt=0, description="Request timeout (seconds)")
    map_timeout: float = Field(default=120.0, gt=0, description="MAP operations timeout (seconds)")
    insecure_skip_verify: bool = Field(default=False, description="Skip TLS verify")
    retry: RetryConfig = Field(default_factory=RetryConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


class MediaContent(BaseModel):
    """Media content (image) to include with a request for governance analysis."""

    source: Literal["base64", "url"] = Field(
        ..., description="How media is provided: 'base64' or 'url'"
    )
    base64_data: str | None = Field(default=None, description="Base64-encoded image data")
    url: str | None = Field(default=None, description="Image URL")
    mime_type: str = Field(..., description="Media content type (e.g., 'image/jpeg')")


class MediaAnalysisResult(BaseModel):
    """Analysis results for a single media item."""

    media_index: int = Field(default=0, description="Index in the request media array")
    sha256_hash: str = Field(default="", description="SHA-256 hash of the image data")
    has_faces: bool = Field(default=False, description="Whether faces were detected")
    face_count: int = Field(default=0, ge=0, description="Number of faces detected")
    has_biometric_data: bool = Field(
        default=False, description="Biometric data detected (GDPR Art. 9)"
    )
    nsfw_score: float = Field(default=0.0, ge=0, le=1, description="NSFW content score")
    violence_score: float = Field(default=0.0, ge=0, le=1, description="Violence content score")
    content_safe: bool = Field(default=True, description="Aggregated content safety flag")
    document_type: str | None = Field(default=None, description="Classified document type")
    is_sensitive_document: bool = Field(default=False, description="Sensitive document flag")
    has_pii: bool = Field(default=False, description="PII detected in image text via OCR")
    pii_types: list[str] = Field(default_factory=list, description="Types of PII detected")
    has_extracted_text: bool = Field(
        default=False, description="Whether text was extracted from image via OCR"
    )
    extracted_text_length: int = Field(
        default=0, ge=0, description="Length of extracted text in characters"
    )
    estimated_cost_usd: float = Field(default=0.0, ge=0, description="Analysis cost for this item")
    warnings: list[str] = Field(default_factory=list, description="Governance warnings")


class MediaAnalysisResponse(BaseModel):
    """Aggregated media analysis results in the response."""

    results: list[MediaAnalysisResult] = Field(
        default_factory=list, description="Per-item analysis results"
    )
    total_cost_usd: float = Field(default=0.0, ge=0, description="Total analysis cost")
    analysis_time_ms: int = Field(default=0, ge=0, description="Total analysis time (ms)")


class ClientRequest(BaseModel):
    """Request to AxonFlow Agent."""

    query: str = Field(..., description="Query or prompt")
    user_token: str = Field(..., description="User token for auth")
    client_id: str | None = Field(default=None, description="Client ID (optional)")
    request_type: str = Field(..., description="Request type")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    media: list[MediaContent] | None = Field(
        default=None, description="Optional media for multimodal governance"
    )


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

    @field_validator("policies_evaluated", "static_checks", mode="before")
    @classmethod
    def _coerce_none_to_list(cls, v: object) -> object:
        return v if v is not None else []


class BudgetInfo(BaseModel):
    """Budget enforcement status information (Issue #1082).

    Returned when a budget check is performed, showing current usage
    relative to budget limits.
    """

    budget_id: str | None = Field(default=None, description="Budget ID")
    budget_name: str | None = Field(default=None, description="Budget name")
    used_usd: float = Field(default=0.0, description="Current usage in USD")
    limit_usd: float = Field(default=0.0, description="Budget limit in USD")
    percentage: float = Field(default=0.0, description="Usage percentage (0-100+)")
    exceeded: bool = Field(default=False, description="Whether budget is exceeded")
    action: str | None = Field(default=None, description="Action on exceed: warn, block, downgrade")


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
    budget_info: BudgetInfo | None = Field(default=None, description="Budget status (Issue #1082)")
    media_analysis: MediaAnalysisResponse | None = Field(
        default=None, description="Media governance results"
    )

    def model_post_init(self, __context: Any) -> None:
        """Detect nested data.success=false and surface error."""
        if isinstance(self.data, dict):
            data_success = self.data.get("success")
            if data_success is False:
                data_error = self.data.get("error")
                if data_error and not self.error:
                    object.__setattr__(self, "error", str(data_error))
                object.__setattr__(self, "success", False)
            if not self.result and isinstance(self.data.get("result"), str):
                object.__setattr__(self, "result", self.data["result"])
            if not self.plan_id and isinstance(self.data.get("plan_id"), str):
                object.__setattr__(self, "plan_id", self.data["plan_id"])
            if not self.metadata and isinstance(self.data.get("metadata"), dict):
                object.__setattr__(self, "metadata", self.data["metadata"])


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
    last_check: str | None = Field(default=None, description="When last health check was performed")


class ConnectorHealthStatus(BaseModel):
    """Health status of an installed connector."""

    healthy: bool = Field(description="Overall health status")
    latency: int = Field(default=0, description="Connection latency in nanoseconds")
    details: dict[str, str] = Field(default_factory=dict, description="Additional diagnostic info")
    timestamp: str = Field(default="", description="When health check was performed")
    error: str | None = Field(default=None, description="Error message if unhealthy")


class ConnectorInstallRequest(BaseModel):
    """Request to install an MCP connector."""

    connector_id: str
    name: str
    tenant_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class PolicyMatchInfo(BaseModel):
    """Information about a policy match during evaluation."""

    policy_id: str = Field(..., description="Unique policy identifier")
    policy_name: str = Field(..., description="Human-readable policy name")
    category: str = Field(..., description="Policy category")
    severity: str = Field(..., description="Match severity")
    action: str = Field(..., description="Action taken")


class ExfiltrationCheckInfo(BaseModel):
    """Information about exfiltration limit checks (Issue #966).

    Helps prevent large-scale data extraction via MCP queries.
    """

    rows_returned: int = Field(default=0, ge=0, description="Number of rows in the response")
    row_limit: int = Field(default=0, ge=0, description="Configured max rows per query")
    bytes_returned: int = Field(default=0, ge=0, description="Size of response data in bytes")
    byte_limit: int = Field(default=0, ge=0, description="Configured max bytes per response")
    within_limits: bool = Field(default=True, description="Whether response is within limits")


class DynamicPolicyMatch(BaseModel):
    """Details about a matched dynamic policy."""

    policy_id: str = Field(..., description="Unique policy identifier")
    policy_name: str = Field(default="", description="Human-readable policy name")
    policy_type: str = Field(
        default="",
        description="Type of policy (rate-limit, budget, time-access, role-access, mcp, connector)",
    )
    action: str = Field(default="", description="Action taken (allow, block, log, etc.)")
    reason: str | None = Field(default=None, description="Context for the policy match")


class DynamicPolicyInfo(BaseModel):
    """Information about dynamic policy evaluation (Issue #968).

    Dynamic policies are evaluated by the Orchestrator and can include
    rate limiting, budget controls, time-based access, and role-based access policies.
    """

    policies_evaluated: int = Field(
        default=0, ge=0, description="Number of dynamic policies checked"
    )
    matched_policies: list[DynamicPolicyMatch] = Field(
        default_factory=list, description="Policies that matched"
    )
    orchestrator_reachable: bool = Field(
        default=True, description="Whether the Orchestrator was reachable"
    )
    processing_time_ms: int = Field(
        default=0, ge=0, description="Time taken for dynamic policy evaluation"
    )


class ConnectorPolicyInfo(BaseModel):
    """Policy evaluation information included in MCP responses.

    Provides transparency into policy enforcement decisions for
    request blocking and response redaction.
    """

    policies_evaluated: int = Field(default=0, ge=0, description="Number of policies evaluated")
    blocked: bool = Field(default=False, description="Whether request was blocked")
    block_reason: str | None = Field(default=None, description="Reason if blocked")
    redactions_applied: int = Field(default=0, ge=0, description="Number of redactions applied")
    processing_time_ms: int = Field(default=0, ge=0, description="Policy evaluation time in ms")
    matched_policies: list[PolicyMatchInfo] = Field(
        default_factory=list, description="Policies that matched"
    )
    exfiltration_check: ExfiltrationCheckInfo | None = Field(
        default=None, description="Exfiltration check info (Issue #966)"
    )
    dynamic_policy_info: DynamicPolicyInfo | None = Field(
        default=None, description="Dynamic policy evaluation info (Issue #968)"
    )


class ConnectorResponse(BaseModel):
    """Response from MCP connector query."""

    success: bool
    data: Any | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = Field(default=False, description="Whether any fields were redacted")
    redacted_fields: list[str] = Field(
        default_factory=list, description="JSON paths of redacted fields"
    )
    policy_info: ConnectorPolicyInfo | None = Field(
        default=None, description="Policy evaluation details"
    )

    def was_redacted(self) -> bool:
        """Returns True if any fields were redacted by policy evaluation."""
        return self.redacted


class MCPCheckInputRequest(BaseModel):
    """Request to validate input against MCP policies."""

    connector_type: str
    statement: str
    parameters: dict[str, Any] | None = Field(default=None)
    operation: str = Field(default="execute")


class MCPCheckInputResponse(BaseModel):
    """Result of input policy evaluation."""

    allowed: bool
    block_reason: str | None = Field(default=None)
    policies_evaluated: int = Field(default=0, ge=0)
    policy_info: ConnectorPolicyInfo | None = Field(default=None)


class MCPCheckOutputRequest(BaseModel):
    """Request to validate output against MCP policies."""

    connector_type: str
    response_data: list[dict[str, Any]] | None = Field(default=None)
    message: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)
    row_count: int = Field(default=0, ge=0)


class MCPCheckOutputResponse(BaseModel):
    """Result of output policy evaluation."""

    allowed: bool
    block_reason: str | None = Field(default=None)
    redacted_data: Any | None = Field(default=None)
    policies_evaluated: int = Field(default=0, ge=0)
    exfiltration_info: ExfiltrationCheckInfo | None = Field(default=None)
    policy_info: ConnectorPolicyInfo | None = Field(default=None)


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


class PolicyEvaluationResult(BaseModel):
    """Result of policy evaluation for workflow steps and plan executions.

    Used by MAP (Multi-Agent Planning) and WCP (Workflow Control Plane) to provide
    detailed policy enforcement information (Issues #1019, #1020, #1021).
    """

    allowed: bool = Field(..., description="Whether the action is allowed by policy")
    applied_policies: list[str] = Field(
        default_factory=list, description="List of policy IDs that were applied"
    )
    risk_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Calculated risk score (0.0-1.0)"
    )
    required_actions: list[str] | None = Field(
        default=None, description="Actions required before proceeding (if any)"
    )
    processing_time_ms: int = Field(
        default=0, ge=0, description="Time taken for policy evaluation in milliseconds"
    )
    database_accessed: bool | None = Field(
        default=None, description="Whether a database was accessed during the operation"
    )


class PlanExecutionResponse(BaseModel):
    """Plan execution result."""

    plan_id: str
    status: str  # "running", "completed", "failed", "awaiting_approval"
    workflow_id: str | None = None  # WCP workflow ID for confirm/step mode
    result: str | None = None
    step_results: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration: str | None = None
    policy_info: PolicyEvaluationResult | None = Field(
        default=None, description="Policy evaluation result for the plan execution"
    )


class ExecutionMode(str, Enum):
    """MAP plan execution mode.

    Controls how plan steps are scheduled and executed.
    """

    AUTO = "auto"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    BALANCED = "balanced"
    CONFIRM = "confirm"
    STEP = "step"


class CancelPlanResponse(BaseModel):
    """Response from cancelling a running plan."""

    plan_id: str = Field(..., description="ID of the cancelled plan")
    status: str = Field(..., description="Plan status after cancellation")
    message: str = Field(..., description="Cancellation confirmation message")


class UpdatePlanRequest(BaseModel):
    """Request to update a plan with optimistic concurrency control.

    The expected_version field enables optimistic locking: the update
    will only succeed if the plan's current version matches.
    """

    model_config = ConfigDict(populate_by_name=True)

    expected_version: int = Field(
        ...,
        alias="version",
        description="Expected current version for optimistic locking",
    )
    execution_mode: ExecutionMode | None = Field(
        default=None, description="New execution mode for the plan"
    )
    domain: str | None = Field(default=None, description="New domain for the plan")


class UpdatePlanResponse(BaseModel):
    """Response from updating a plan."""

    plan_id: str = Field(..., description="ID of the updated plan")
    version: int = Field(..., description="New version number after update")
    status: str = Field(..., description="Plan status after update")
    success: bool = Field(..., description="Whether the update succeeded")


class PlanVersionEntry(BaseModel):
    """A single entry in a plan's version history."""

    version: int = Field(..., description="Version number")
    changed_at: str = Field(..., description="ISO timestamp of the change")
    change_type: str = Field(..., description="Type of change (created, updated, etc.)")
    changed_by: str | None = Field(
        default=None,
        description="User or system that made the change",
    )
    change_summary: str | None = Field(
        default=None,
        description="Human-readable summary of changes",
    )


class PlanVersionsResponse(BaseModel):
    """Response containing a plan's version history."""

    plan_id: str = Field(..., description="ID of the plan")
    versions: list[PlanVersionEntry] = Field(
        default_factory=list, description="Version history entries"
    )


class ResumePlanResponse(BaseModel):
    """Response from resuming a paused plan."""

    plan_id: str = Field(..., description="ID of the resumed plan")
    workflow_id: str | None = Field(default=None, description="WCP workflow ID")
    status: str = Field(..., description="Plan status after resume")
    approved: bool | None = Field(default=None, description="Whether the resume was approved")
    message: str | None = Field(default=None, description="Resume confirmation message")
    step_result: dict[str, Any] | None = Field(default=None, description="Result of executed step")
    next_step: int | None = Field(default=None, description="Next step index")
    next_step_name: str | None = Field(default=None, description="Next step name")
    total_steps: int | None = Field(default=None, description="Total number of steps")


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
    requires_redaction: bool = Field(
        default=False,
        description="Whether response requires redaction (PII detected with redact action)",
    )
    approved_data: dict[str, Any] = Field(default_factory=dict)
    policies: list[str] | None = Field(default=None)
    rate_limit_info: RateLimitInfo | None = None
    expires_at: datetime
    block_reason: str | None = None

    @field_validator("policies", mode="before")
    @classmethod
    def policies_default(cls, v: list[str] | None) -> list[str]:
        """Convert None to empty list for policies."""
        return v if v is not None else []


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
# Audit Log Read Types
# =========================================================================


class AuditSearchRequest(BaseModel):
    """Request parameters for searching audit logs.

    All fields are optional - omit to search all logs.

    Attributes:
        user_email: Filter by user email
        client_id: Filter by client/application ID
        start_time: Start of time range to search
        end_time: End of time range to search
        request_type: Filter by request type (e.g., "llm_chat", "policy_check")
        limit: Maximum results to return (default: 100, max: 1000)
        offset: Pagination offset (default: 0)
    """

    user_email: str | None = Field(default=None, description="Filter by user email")
    client_id: str | None = Field(default=None, description="Filter by client ID")
    start_time: datetime | None = Field(default=None, description="Start of time range")
    end_time: datetime | None = Field(default=None, description="End of time range")
    request_type: str | None = Field(default=None, description="Filter by request type")
    limit: int = Field(default=100, ge=1, le=1000, description="Max results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class AuditQueryOptions(BaseModel):
    """Options for GetAuditLogsByTenant.

    Attributes:
        limit: Maximum results to return (default: 50)
        offset: Pagination offset (default: 0)
    """

    limit: int = Field(default=50, ge=1, le=1000, description="Max results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class AuditLogEntry(BaseModel):
    """A single audit log entry.

    Represents an audited request or event in the AxonFlow platform.

    Attributes:
        id: Unique audit log ID
        request_id: Correlation ID for the original request
        timestamp: When the event occurred
        user_email: Email of the user who made the request
        client_id: Client/application that made the request
        tenant_id: Tenant identifier
        request_type: Type of request (e.g., "llm_chat", "sql", "mcp-query")
        query_summary: Summary of the query/request
        success: Whether the request succeeded
        blocked: Whether the request was blocked by policy
        risk_score: Calculated risk score (0.0-1.0)
        provider: LLM provider used (if applicable)
        model: Model used (if applicable)
        tokens_used: Total tokens consumed
        latency_ms: Request latency in milliseconds
        policy_violations: List of violated policy IDs (if any)
        metadata: Additional context
    """

    id: str = Field(..., description="Unique audit log ID")
    request_id: str = Field(default="", description="Correlation ID")
    timestamp: datetime = Field(..., description="When event occurred")
    user_email: str = Field(default="", description="User email")
    client_id: str = Field(default="", description="Client ID")
    tenant_id: str = Field(default="", description="Tenant ID")
    request_type: str = Field(default="", description="Request type")
    query_summary: str = Field(default="", description="Query summary")
    success: bool = Field(default=True, description="Request succeeded")
    blocked: bool = Field(default=False, description="Request was blocked")
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Risk score")
    provider: str = Field(default="", description="LLM provider")
    model: str = Field(default="", description="Model used")
    tokens_used: int = Field(default=0, ge=0, description="Tokens consumed")
    latency_ms: int = Field(default=0, ge=0, description="Latency in ms")
    policy_violations: list[str] = Field(default_factory=list, description="Violated policies")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class AuditSearchResponse(BaseModel):
    """Response from an audit search.

    Attributes:
        entries: Audit log entries matching the search
        total: Total number of matching entries (for pagination)
        limit: Limit that was applied
        offset: Offset that was applied
    """

    entries: list[AuditLogEntry] = Field(default_factory=list, description="Audit entries")
    total: int = Field(default=0, ge=0, description="Total matching entries")
    limit: int = Field(default=100, ge=1, description="Limit applied")
    offset: int = Field(default=0, ge=0, description="Offset applied")


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

    @field_validator("executions", mode="before")
    @classmethod
    def handle_null_executions(cls, v: Any) -> list[Any]:
        """Handle null executions from API (returns empty list instead)."""
        return v if v is not None else []


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


# ========================================
# COST CONTROLS TYPES
# ========================================


class BudgetScope(str, Enum):
    """Budget scope determines what entity the budget applies to."""

    ORGANIZATION = "organization"
    TEAM = "team"
    AGENT = "agent"
    WORKFLOW = "workflow"
    USER = "user"


class BudgetPeriod(str, Enum):
    """Budget period determines the time window for budget tracking."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class BudgetOnExceed(str, Enum):
    """Action to take when budget is exceeded."""

    WARN = "warn"
    BLOCK = "block"
    DOWNGRADE = "downgrade"


class CreateBudgetRequest(BaseModel):
    """Request to create a new budget."""

    id: str = Field(..., min_length=1, description="Budget ID")
    name: str = Field(..., min_length=1, description="Budget name")
    scope: BudgetScope = Field(..., description="Budget scope")
    limit_usd: float = Field(..., gt=0, description="Budget limit in USD")
    period: BudgetPeriod = Field(..., description="Budget period")
    on_exceed: BudgetOnExceed = Field(..., description="Action when exceeded")
    alert_thresholds: list[int] = Field(default_factory=list, description="Alert thresholds")
    scope_id: str | None = Field(default=None, description="Scope entity ID")


class UpdateBudgetRequest(BaseModel):
    """Request to update an existing budget."""

    name: str | None = Field(default=None, description="New budget name")
    limit_usd: float | None = Field(default=None, gt=0, description="New limit in USD")
    on_exceed: BudgetOnExceed | None = Field(default=None, description="New action")
    alert_thresholds: list[int] | None = Field(default=None, description="New thresholds")


class ListBudgetsOptions(BaseModel):
    """Options for listing budgets."""

    scope: BudgetScope | None = Field(default=None, description="Filter by scope")
    limit: int = Field(default=50, ge=1, le=100, description="Page size")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class Budget(BaseModel):
    """A budget entity."""

    id: str = Field(..., description="Budget ID")
    name: str = Field(..., description="Budget name")
    scope: str = Field(..., description="Budget scope")
    limit_usd: float = Field(..., description="Budget limit in USD")
    period: str = Field(..., description="Budget period")
    on_exceed: str = Field(..., description="Action when exceeded")
    alert_thresholds: list[int] = Field(default_factory=list, description="Alert thresholds")
    enabled: bool = Field(default=True, description="Whether budget is enabled")
    scope_id: str | None = Field(default=None, description="Scope entity ID")
    created_at: str | None = Field(default=None, description="Created timestamp")
    updated_at: str | None = Field(default=None, description="Updated timestamp")


class BudgetsResponse(BaseModel):
    """Response containing a list of budgets."""

    budgets: list[Budget] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


class BudgetStatus(BaseModel):
    """Current status of a budget."""

    budget: Budget = Field(..., description="The budget")
    used_usd: float = Field(default=0.0, ge=0, description="Amount used in USD")
    remaining_usd: float = Field(default=0.0, description="Remaining amount in USD")
    percentage: float = Field(default=0.0, ge=0, description="Usage percentage")
    is_exceeded: bool = Field(default=False, description="Whether budget is exceeded")
    is_blocked: bool = Field(default=False, description="Whether budget is blocking")
    period_start: str = Field(..., description="Period start timestamp")
    period_end: str = Field(..., description="Period end timestamp")


class BudgetAlert(BaseModel):
    """A budget alert."""

    id: str = Field(..., description="Alert ID")
    budget_id: str = Field(..., description="Budget ID")
    alert_type: str = Field(..., description="Alert type")
    threshold: int = Field(..., description="Threshold that was reached")
    percentage_reached: float = Field(..., description="Percentage when alert triggered")
    amount_usd: float = Field(..., description="Amount when alert triggered")
    message: str = Field(..., description="Alert message")
    created_at: str = Field(..., description="Alert timestamp")


class BudgetAlertsResponse(BaseModel):
    """Response containing budget alerts."""

    alerts: list[BudgetAlert] | None = Field(default=None)
    count: int = Field(default=0, ge=0)


class BudgetCheckRequest(BaseModel):
    """Request to check if a request is allowed by budgets."""

    org_id: str | None = Field(default=None, description="Organization ID")
    team_id: str | None = Field(default=None, description="Team ID")
    agent_id: str | None = Field(default=None, description="Agent ID")
    workflow_id: str | None = Field(default=None, description="Workflow ID")
    user_id: str | None = Field(default=None, description="User ID")


class BudgetDecision(BaseModel):
    """Budget decision result."""

    allowed: bool = Field(..., description="Whether request is allowed")
    action: str | None = Field(default=None, description="Suggested action")
    message: str | None = Field(default=None, description="Decision message")
    budgets: list[Budget] | None = Field(default=None, description="Related budgets")


class UsageSummary(BaseModel):
    """Usage summary for a period."""

    total_cost_usd: float = Field(default=0.0, ge=0, description="Total cost in USD")
    total_requests: int = Field(default=0, ge=0, description="Total requests")
    total_tokens_in: int = Field(default=0, ge=0, description="Total input tokens")
    total_tokens_out: int = Field(default=0, ge=0, description="Total output tokens")
    average_cost_per_request: float = Field(default=0.0, ge=0, description="Avg cost per request")
    period: str = Field(..., description="Period type")
    period_start: str = Field(..., description="Period start timestamp")
    period_end: str = Field(..., description="Period end timestamp")


class UsageBreakdownItem(BaseModel):
    """An item in a usage breakdown."""

    group_value: str = Field(..., description="Group dimension value")
    cost_usd: float = Field(default=0.0, ge=0, description="Cost in USD")
    percentage: float = Field(default=0.0, ge=0, description="Percentage of total")
    request_count: int = Field(default=0, ge=0, description="Request count")
    tokens_in: int = Field(default=0, ge=0, description="Input tokens")
    tokens_out: int = Field(default=0, ge=0, description="Output tokens")


class UsageBreakdown(BaseModel):
    """Usage breakdown by a grouping dimension."""

    group_by: str = Field(..., description="Grouping dimension")
    total_cost_usd: float = Field(default=0.0, ge=0, description="Total cost in USD")
    items: list[UsageBreakdownItem] | None = Field(default=None)
    period: str | None = Field(default=None, description="Period type")
    period_start: str | None = Field(default=None, description="Period start timestamp")
    period_end: str | None = Field(default=None, description="Period end timestamp")


class ListUsageRecordsOptions(BaseModel):
    """Options for listing usage records."""

    limit: int = Field(default=50, ge=1, le=100, description="Page size")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    provider: str | None = Field(default=None, description="Filter by provider")
    model: str | None = Field(default=None, description="Filter by model")


class UsageRecord(BaseModel):
    """A single usage record."""

    id: str = Field(..., description="Record ID")
    provider: str = Field(..., description="LLM provider")
    model: str = Field(..., description="Model name")
    tokens_in: int = Field(default=0, ge=0, description="Input tokens")
    tokens_out: int = Field(default=0, ge=0, description="Output tokens")
    cost_usd: float = Field(default=0.0, ge=0, description="Cost in USD")
    request_id: str | None = Field(default=None, description="Request ID")
    org_id: str | None = Field(default=None, description="Organization ID")
    agent_id: str | None = Field(default=None, description="Agent ID")
    timestamp: str | None = Field(default=None, description="Record timestamp")


class UsageRecordsResponse(BaseModel):
    """Response containing usage records."""

    records: list[UsageRecord] | None = Field(default=None)
    total: int = Field(default=0, ge=0)


class ModelPricing(BaseModel):
    """Model pricing information."""

    input_per_1k: float = Field(..., ge=0, description="Cost per 1K input tokens")
    output_per_1k: float = Field(..., ge=0, description="Cost per 1K output tokens")


class PricingInfo(BaseModel):
    """Pricing information for a provider/model."""

    provider: str = Field(..., description="LLM provider")
    model: str = Field(..., description="Model name")
    pricing: ModelPricing = Field(..., description="Pricing details")


class PricingListResponse(BaseModel):
    """Response containing pricing information."""

    pricing: list[PricingInfo] = Field(default_factory=list)


# =========================================================================
# Plan Rollback Types (Feature 7)
# =========================================================================


class RollbackPlanResponse(BaseModel):
    """Response from rolling back a plan to a previous version."""

    plan_id: str = Field(..., description="ID of the plan")
    version: int = Field(..., description="Version after rollback")
    previous_version: int = Field(..., description="Version before rollback")
    status: str = Field(..., description="Plan status after rollback")


# =========================================================================
# Webhook Types (Feature 7)
# =========================================================================


class WebhookSubscription(BaseModel):
    """A webhook subscription."""

    id: str = Field(..., description="Webhook subscription ID")
    url: str = Field(..., description="Webhook URL")
    events: list[str] = Field(default_factory=list, description="Events to subscribe to")
    active: bool = Field(default=True, description="Whether the webhook is active")
    created_at: str = Field(..., description="When the webhook was created")
    updated_at: str = Field(..., description="When the webhook was last updated")


class ListWebhooksResponse(BaseModel):
    """Response containing a list of webhooks."""

    webhooks: list[WebhookSubscription] = Field(
        default_factory=list, description="List of webhook subscriptions"
    )
    total: int = Field(default=0, ge=0, description="Total count of webhooks")


# =========================================================================
# Media Governance Config Types
# =========================================================================


class MediaGovernanceConfig(BaseModel):
    """Per-tenant media governance configuration.

    Controls whether media analysis is enabled and which analyzers
    are allowed for a given tenant.
    """

    tenant_id: str = Field(default="", description="Tenant ID")
    enabled: bool = Field(default=False, description="Whether media analysis is enabled")
    allowed_analyzers: list[str] = Field(
        default_factory=list, description="List of allowed analyzer IDs"
    )
    updated_at: str = Field(default="", description="Last updated timestamp")
    updated_by: str = Field(default="", description="User who last updated the config")


class MediaGovernanceStatus(BaseModel):
    """Platform-level media governance status.

    Reports availability and default configuration for media governance.
    """

    available: bool = Field(default=False, description="Whether media governance is available")
    enabled_by_default: bool = Field(
        default=False, description="Whether media governance is enabled by default for new tenants"
    )
    per_tenant_control: bool = Field(
        default=False, description="Whether per-tenant media governance control is supported"
    )
    tier: str = Field(default="", description="License tier (community, enterprise, etc.)")


class UpdateMediaGovernanceConfigRequest(BaseModel):
    """Request to update per-tenant media governance configuration."""

    enabled: bool | None = Field(default=None, description="Enable or disable media analysis")
    allowed_analyzers: list[str] | None = Field(
        default=None, description="List of allowed analyzer IDs"
    )


# =========================================================================
# Media Governance Category Constants
# =========================================================================

CATEGORY_MEDIA_SAFETY: str = "media-safety"
CATEGORY_MEDIA_BIOMETRIC: str = "media-biometric"
CATEGORY_MEDIA_DOCUMENT: str = "media-document"
CATEGORY_MEDIA_PII: str = "media-pii"
