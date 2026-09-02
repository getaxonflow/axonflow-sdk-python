"""AxonFlow SDK Main Client.

The primary interface for interacting with AxonFlow governance platform.
Supports both async and sync usage patterns.

Example:
    >>> from axonflow import AxonFlow
    >>>
    >>> # Async usage (enterprise with authentication)
    >>> async with AxonFlow(endpoint="...", client_id="...", client_secret="...") as client:
    ...     result = await client.proxy_llm_call("user-token", "What is AI?", "chat")
    ...     print(result.data)
    >>>
    >>> # Async usage (community/self-hosted - no auth required)
    >>> async with AxonFlow(endpoint="http://localhost:8080") as client:
    ...     result = await client.proxy_llm_call("user-token", "What is AI?", "chat")
    ...     print(result.data)
    >>>
    >>> # Sync usage
    >>> client = AxonFlow.sync(endpoint="...", client_id="...", client_secret="...")
    >>> result = client.proxy_llm_call("user-token", "What is AI?", "chat")
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Coroutine, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from axonflow.masfeat import (
        AISystemRegistry,
        FEATAssessment,
        Finding,
        KillSwitch,
        KillSwitchEvent,
        RegistrySummary,
    )

from urllib.parse import quote, urlencode

import httpx
import structlog
from cachetools import TTLCache
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from axonflow import masfeat
from axonflow._version import __version__ as _SDK_VERSION
from axonflow.authzen import AuthZENDecision, build_envelope, evaluate_envelope
from axonflow.authzen_types_gen import AuthZENBulk, AuthZENRequest  # noqa: TC001
from axonflow.code_governance import (
    CodeGovernanceMetrics,
    ConfigureGitProviderRequest,
    ConfigureGitProviderResponse,
    CreatePRRequest,
    CreatePRResponse,
    ExportOptions,
    ExportResponse,
    GitProviderType,
    ListGitProvidersResponse,
    ListPRsOptions,
    ListPRsResponse,
    PRRecord,
    ValidateGitProviderRequest,
    ValidateGitProviderResponse,
)
from axonflow.decisions import (
    DecisionExplanation,
    DecisionSummary,
    ListDecisionsOptions,
)
from axonflow.exceptions import (
    AuthenticationError,
    AxonFlowError,
    BudgetExceededError,
    ConnectionError,
    ConnectorError,
    IdempotencyKeyMismatchError,
    ObligationNotFulfillableError,
    PlanExecutionError,
    PolicyViolationError,
    RateLimitError,
    TimeoutError,
    UpgradeInfo,
    VersionConflictError,
)
from axonflow.execution import (
    ExecutionStatus,
    ExecutionStatusValue,
    ExecutionType,
    StepStatusValue,
    UnifiedApprovalStatus,
    UnifiedGateDecision,
    UnifiedListExecutionsRequest,
    UnifiedListExecutionsResponse,
    UnifiedStepStatus,
    UnifiedStepType,
)
from axonflow.heartbeat import maybe_send_heartbeat
from axonflow.hitl import (
    HITLApprovalRequest,
    HITLCreateInput,
    HITLQueueListOptions,
    HITLQueueListResponse,
    HITLReviewInput,
    HITLStats,
)
from axonflow.pep import CONTENT_TYPE_TEXT, OBLIGATION_REDACT_PII, PHASE_REQUEST, VERDICT_ALLOW
from axonflow.pep import DECIDE_PATH as PEP_DECIDE_PATH
from axonflow.pep import REQUEST_REDACTION_PATH as PEP_REQUEST_REDACTION_PATH
from axonflow.pep import _endpoint_path_matches as pep_endpoint_path_matches
from axonflow.policies import (
    CreateDynamicPolicyRequest,
    CreatePolicyOverrideRequest,
    CreateStaticPolicyRequest,
    DynamicPolicy,
    EffectivePoliciesOptions,
    ListDynamicPoliciesOptions,
    ListStaticPoliciesOptions,
    PolicyCategory,  # noqa: F401 - used in docstrings
    PolicyOverride,
    PolicyTier,  # noqa: F401 - used in docstrings
    PolicyVersion,
    StaticPolicy,
    TestPatternResult,
    UpdateDynamicPolicyRequest,
    UpdateStaticPolicyRequest,
)
from axonflow.read_identity import (
    read_scope_error_for,
    read_scope_of,
    refuse_vacuous_scoped_page,
    stamp_read_identity,
    use_read_identity,
)
from axonflow.types import (
    AuditLogEntry,
    AuditQueryOptions,
    AuditResult,
    AuditSearchRequest,
    AuditSearchResponse,
    AuditToolCallRequest,
    AuditToolCallResponse,
    AxonFlowConfig,
    Budget,
    BudgetAlertsResponse,
    BudgetCheckRequest,
    BudgetDecision,
    BudgetsResponse,
    BudgetStatus,
    CacheConfig,
    CancelPlanResponse,
    CircuitBreakerConfig,
    CircuitBreakerConfigUpdate,
    CircuitBreakerHistoryEntry,
    CircuitBreakerHistoryResponse,
    CircuitBreakerStatusResponse,
    ClientRequest,
    ClientResponse,
    ConnectorHealthStatus,
    ConnectorInstallRequest,
    ConnectorMetadata,
    ConnectorPolicyInfo,
    ConnectorResponse,
    CreateBudgetRequest,
    DecideRequest,
    DecideResponse,
    ExecutionDetail,
    ExecutionExportOptions,
    ExecutionMode,
    ExecutionSnapshot,
    ImpactReportResponse,
    ListBudgetsOptions,
    ListExecutionsOptions,
    ListExecutionsResponse,
    ListUsageRecordsOptions,
    ListWebhooksResponse,
    LLMProvider,
    LLMProviderHealth,
    LLMProviderListResponse,
    MCPCheckInputResponse,
    MCPCheckOutputResponse,
    MediaContent,
    MediaGovernanceConfig,
    MediaGovernanceStatus,
    Mode,
    PaginationMeta,
    PlanExecutionResponse,
    PlanResponse,
    PlanStep,
    PlanVersionsResponse,
    PolicyApprovalResult,
    PolicyConflictResponse,
    PolicyEvaluationResult,
    PricingInfo,
    PricingListResponse,
    RateLimitInfo,
    ResumePlanResponse,
    RetryConfig,
    RollbackPlanResponse,
    SimulatePoliciesResponse,
    TimelineEntry,
    TokenUsage,
    UpdateBudgetRequest,
    UpdateMediaGovernanceConfigRequest,
    UpdatePlanRequest,
    UpdatePlanResponse,
    UsageBreakdown,
    UsageRecordsResponse,
    UsageSummary,
    WebhookSubscription,
)
from axonflow.workflow import (
    ApprovalStatus,
    ApproveStepResponse,
    CheckpointListResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    GateDecision,
    ListWorkflowsOptions,
    ListWorkflowsResponse,
    MarkStepCompletedRequest,
    PendingApproval,
    PendingApprovalsResponse,
    RejectStepResponse,
    ResumeFromCheckpointResponse,
    RetryContext,
    StepGateRequest,
    StepGateResponse,
    StepType,
    WorkflowSource,
    WorkflowStatus,
    WorkflowStatusResponse,
    WorkflowStepInfo,
)

if TYPE_CHECKING:
    from types import TracebackType

logger = structlog.get_logger(__name__)


def _parse_datetime(value: str) -> datetime:
    """Parse ISO format datetime string.

    Python 3.9's fromisoformat() doesn't handle 'Z' suffix for UTC.
    This helper replaces 'Z' with '+00:00' for compatibility.

    Also normalizes fractional seconds to exactly 6 digits (microseconds)
    since Python 3.9's fromisoformat() requires 0, 3, or 6 fractional digits.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # Normalize fractional seconds to exactly 6 digits for Python 3.9 compatibility
    # Handles cases like .35012 (5 digits) -> .350120, or .123456789 (9 digits) -> .123456
    def normalize_fractional_seconds(match: re.Match[str]) -> str:
        frac = match.group(1)
        suffix = match.group(2)
        # Pad with zeros if less than 6 digits, truncate if more than 6
        normalized = frac[:6].ljust(6, "0")
        return f".{normalized}{suffix}"

    value = re.sub(r"\.(\d+)([+-]|$)", normalize_fractional_seconds, value)

    return datetime.fromisoformat(value)


# TypeVar for generic _run_sync method in SyncAxonFlow
T = TypeVar("T")


def _parse_idempotency_key_mismatch(
    response: httpx.Response,
    *,
    workflow_id: str,
    step_id: str,
) -> IdempotencyKeyMismatchError | None:
    """Inspect a 409 response body for IDEMPOTENCY_KEY_MISMATCH.

    Returns a typed :class:`IdempotencyKeyMismatchError` if the body matches the
    contract shape (``error.code == "IDEMPOTENCY_KEY_MISMATCH"``), otherwise ``None``.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if not isinstance(err, dict) or err.get("code") != "IDEMPOTENCY_KEY_MISMATCH":
        return None
    details = err.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    return IdempotencyKeyMismatchError(
        message=str(err.get("message", "idempotency_key mismatch")),
        workflow_id=str(details.get("workflow_id") or workflow_id),
        step_id=str(details.get("step_id") or step_id),
        expected_idempotency_key=str(details.get("expected_idempotency_key", "")),
        received_idempotency_key=str(details.get("received_idempotency_key", "")),
    )


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a semver string into a tuple of ints for correct numeric comparison."""
    parts: list[int] = []
    for part in v.split("."):
        # Strip pre-release suffix (e.g., "0-beta" -> "0")
        numeric = part.split("-")[0].split("+")[0]
        try:
            parts.append(int(numeric))
        except ValueError:
            parts.append(0)
    return tuple(parts)


@dataclass
class PlatformCapability:
    """Describes a feature supported by the platform."""

    name: str
    since: str
    description: str


@dataclass
class SDKCompatibility:
    """Per-language SDK version compatibility returned by the platform.

    The platform ``/health`` endpoint returns these as per-language maps
    (e.g. ``{"go": "5.0.0", "python": "6.0.0", ...}``). Older SDK builds
    typed this as a single string and crashed when parsing the dict; keeping
    the field as a map aligns Python with Java + TypeScript SDKs.
    """

    min_sdk_version: dict[str, str]
    recommended_sdk_version: dict[str, str]

    def min_sdk_version_for(self, language: str) -> str:
        """Minimum SDK version required for ``language`` (empty string if unknown)."""
        return self.min_sdk_version.get(language, "")

    def recommended_sdk_version_for(self, language: str) -> str:
        """Recommended SDK version for ``language`` (empty string if unknown)."""
        return self.recommended_sdk_version.get(language, "")


@dataclass
class HealthResponse:
    """Detailed health check response from the platform."""

    status: str
    service: str
    version: str
    capabilities: list[PlatformCapability]
    sdk_compatibility: SDKCompatibility | None = None

    def has_capability(self, name: str) -> bool:
        """Check if the platform supports a named capability."""
        return any(c.name == name for c in self.capabilities)


def _parse_pending_approvals_response(response: dict[str, Any]) -> PendingApprovalsResponse:
    """Parse the server pending-approvals response into the typed model.

    Shared by ``get_pending_approvals`` (WCP plane) and
    ``get_pending_plan_approvals`` (MAP plane) — identical wire shape, the
    difference is just whether ``plan_id`` is populated per-entry.
    """
    approvals = [
        PendingApproval(
            workflow_id=a["workflow_id"],
            workflow_name=a["workflow_name"],
            plan_id=a.get("plan_id"),
            step_id=a["step_id"],
            step_index=a.get("step_index", 0),
            step_name=a.get("step_name"),
            step_type=a.get("step_type"),
            decision=a.get("decision", "require_approval"),
            decision_reason=a.get("decision_reason"),
            policies_matched=a.get("policies_matched"),
            step_input=a.get("step_input"),
            approval_status=a.get("approval_status"),
            created_at=a["created_at"],
        )
        for a in response.get("pending_approvals", [])
    ]
    return PendingApprovalsResponse(
        pending_approvals=approvals,
        count=response.get("count", len(approvals)),
    )


def _build_list_decisions_query(opts: ListDecisionsOptions | None) -> str:
    """Serialize :class:`ListDecisionsOptions` into the URL query string.

    ``None`` values are omitted so the platform applies its tier-default
    page. Stable field order so test mocks can match the URL exactly.
    """
    if opts is None:
        return ""
    pairs: list[tuple[str, str]] = []
    if opts.since is not None:
        # Use the "Z" suffix for UTC; isoformat() emits +00:00 which
        # would urlencode to %2B00%3A00 and over-noise the URL. The
        # platform parses both, but the Z form matches the explain
        # endpoint's response shape byte-for-byte.
        ts = opts.since
        if ts.utcoffset() is not None:
            ts = ts.astimezone(tz=ts.tzinfo).replace(tzinfo=None)
        pairs.append(("since", ts.strftime("%Y-%m-%dT%H:%M:%SZ")))
    if opts.decision is not None:
        pairs.append(("decision", opts.decision))
    if opts.policy_id is not None:
        pairs.append(("policy_id", opts.policy_id))
    if opts.tool_signature is not None:
        pairs.append(("tool_signature", opts.tool_signature))
    if opts.limit is not None:
        pairs.append(("limit", str(opts.limit)))
    return urlencode(pairs)


def _build_audit_search_body(request: AuditSearchRequest) -> dict[str, Any]:
    """Build the POST /api/v1/audit/search body from an AuditSearchRequest.

    Extracted from ``AxonFlow.search_audit_logs`` to keep that method under
    the branch-count lint threshold. Only non-empty / non-default fields are
    emitted — the wire contract is "omit fields you don't care about".
    """
    body: dict[str, Any] = {"limit": request.limit}
    if request.user_email:
        body["user_email"] = request.user_email
    if request.client_id:
        body["client_id"] = request.client_id
    if request.start_time:
        body["start_time"] = request.start_time.isoformat()
    if request.end_time:
        body["end_time"] = request.end_time.isoformat()
    if request.action:
        body["action"] = request.action
    # Deprecated (#3254): the 9.x server does not read request_type as a
    # search filter. Still sent when set (harmless, ignored) until the next
    # major removes the field.
    if request.request_type:
        body["request_type"] = request.request_type
    if request.decision_id:
        body["decision_id"] = request.decision_id
    if request.policy_name:
        body["policy_name"] = request.policy_name
    if request.override_id:
        body["override_id"] = request.override_id
    if request.offset > 0:
        body["offset"] = request.offset
    return body


# Sub-namespaces the client binds LAZILY, each caching a reference to the client
# that created it. as_user() resets every one, because copying such a reference
# hands the derived client a namespace that still calls through the PARENT —
# with the parent's identity. Add to this list when adding a lazy namespace; the
# guard is a list precisely so that adding one is a conscious act.
_LAZY_NAMESPACE_SLOTS = ("_masfeat",)


class AxonFlow:
    """Main AxonFlow client for AI governance.

    This client provides async-first API for interacting with AxonFlow Agent.
    All methods are async by default, with sync wrappers available via `.sync()`.

    Attributes:
        config: Client configuration
    """

    __slots__ = (
        "_config",
        "_http_client",
        "_map_http_client",
        "_cache",
        "_logger",
        "_session_cookie",
        "_masfeat",
    )

    def __init__(
        self,
        endpoint: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        mode: Mode | str = Mode.PRODUCTION,
        debug: bool = False,
        timeout: float = 60.0,
        map_timeout: float = 120.0,
        insecure_skip_verify: bool = False,
        retry_config: RetryConfig | None = None,
        cache_enabled: bool = True,
        cache_ttl: float = 60.0,
        cache_max_size: int = 1000,
        user_token: str | None = None,
    ) -> None:
        """Initialize AxonFlow client.

        Args:
            endpoint: AxonFlow endpoint URL. Can also be set via AXONFLOW_AGENT_URL env var.
            client_id: Client ID (optional for community/self-hosted mode)
            client_secret: Client secret (optional for community/self-hosted mode)
            mode: Operation mode (production or sandbox). Sandbox-mode no longer
                suppresses telemetry as of v8.0 — pings fire and are tagged
                ``stream="sandbox"`` server-side.
            debug: Enable debug logging
            timeout: Request timeout in seconds
            map_timeout: Timeout for MAP operations in seconds (default: 120s)
                        MAP operations involve multiple LLM calls and need longer timeouts
            insecure_skip_verify: Skip TLS verification (dev only)
            retry_config: Retry configuration
            cache_enabled: Enable response caching
            cache_ttl: Cache TTL in seconds
            cache_max_size: Maximum cache entries
            user_token: Per-user identity for the READ path, sent as
                ``X-User-Token``. ``client_id``/``client_secret`` say which
                ORGANIZATION is asking; this says WHO. Since platform #2922
                ``explain_decision``, ``list_decisions`` and the audit/override
                reads are scoped to it — an enterprise stack returns ZERO rows
                to a caller that presents none. Override per call with the
                ``user_token=`` keyword on the read. See
                :mod:`axonflow.read_identity`.

        Note:
            For community/self-hosted deployments, client_id and client_secret can be omitted.
            The SDK will work without authentication headers in this mode.

            As of v1.0.0, all routes go through a single endpoint (Single Entry Point Architecture).
        """
        # Try mode: auto-connect to try.getaxonflow.com (must be checked before endpoint validation)
        if os.environ.get("AXONFLOW_TRY") == "1":
            resolved_endpoint = "https://try.getaxonflow.com"
            if not client_id:
                msg = "client_id is required in try mode (AXONFLOW_TRY=1)"
                raise TypeError(msg)
        else:
            # Support AXONFLOW_AGENT_URL env var for backwards compatibility
            resolved_endpoint = endpoint or os.environ.get("AXONFLOW_AGENT_URL") or ""
            if not resolved_endpoint:
                msg = "endpoint is required (or set AXONFLOW_AGENT_URL environment variable)"
                raise TypeError(msg)

        if isinstance(mode, str):
            mode = Mode(mode)

        self._config = AxonFlowConfig(
            endpoint=resolved_endpoint.rstrip("/"),
            client_id=client_id,
            client_secret=client_secret,
            mode=mode,
            debug=debug,
            timeout=timeout,
            map_timeout=map_timeout,
            insecure_skip_verify=insecure_skip_verify,
            retry=retry_config or RetryConfig(),
            cache=CacheConfig(enabled=cache_enabled, ttl=cache_ttl, max_size=cache_max_size),
            user_token=user_token,
        )

        # Configure SSL verification
        verify_ssl: bool = not insecure_skip_verify

        # Build headers.
        # ADR-050 §4: every governed request to the agent carries
        # X-Axonflow-Client so the agent can derive request scope (sdk) and
        # validate it against the token's aud.scope via HasScope(). Sourced
        # from the bundled _SDK_VERSION constant; no env override (the
        # consumer doesn't get to spoof its own client identity to the agent).
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"axonflow-sdk-python/{_SDK_VERSION}",
            "X-Axonflow-Client": f"sdk-python/{_SDK_VERSION}",
        }
        # Always send Basic auth — server derives tenant from clientId.
        # Uses effective client_id ("community" default when not configured).
        # Reject client_secret without client_id — licensed mode must specify tenant.
        if client_secret and not client_id:
            msg = (
                "client_id is required when client_secret is set. "
                "Set client_id to your tenant identity to avoid "
                "data being stored under the wrong tenant."
            )
            raise ValueError(msg)
        effective_client_id = client_id or "community"
        credentials = f"{effective_client_id}:{client_secret or ''}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
        # X-Client-ID (v9): server-side identity decisions don't have to
        # re-decode Basic auth. The agent's apiAuthMiddleware overwrites
        # the header with its auth-derived value, so caller-supplied
        # values are harmless (no spoofing surface).
        headers["X-Client-ID"] = effective_client_id

        # The read-path per-user identity (X-User-Token) is stamped by a
        # request event hook rather than baked into the default headers above.
        # That is the SDK's ONE identity site: every request either client
        # makes passes through it, and the platform likewise reads the header
        # once in the middleware in front of every proxied route rather than
        # per route. It has to be a hook and not a default header because a
        # per-call identity must be able to REMOVE the client-wide one for one
        # read (a default header cannot be unset per request, and a
        # present-but-empty header is a different thing to send). See
        # axonflow.read_identity.
        async def _stamp_identity(request: httpx.Request) -> None:
            # The endpoint is passed so the identity is only ever sent THERE.
            # httpx re-runs request event hooks on a redirected request, so a
            # hook that stamped unconditionally would re-add the per-user
            # credential to a host the caller never named — on exactly the hop
            # where httpx drops Authorization. See stamp_read_identity.
            stamp_read_identity(self._config.user_token, request, endpoint=self._config.endpoint)

        identity_hooks: dict[str, list[Any]] = {"request": [_stamp_identity]}

        # NO explicit `transport=` here, deliberately. httpx only builds its
        # environment proxy map when it constructs the transport itself
        # (`allow_env_proxies = trust_env and transport is None`), so passing
        # one leaves `_mounts` EMPTY and every customer behind an egress proxy
        # loses connectivity — a total outage on upgrade, caused by a change
        # about read scoping. Measured: with HTTPS_PROXY set, `_mounts` is a
        # one-entry map without an explicit transport and `{}` with one.
        #
        # as_user() therefore borrows this client's transport AND its mounts
        # rather than the whole client, which keeps both the pool and the proxy
        # behaviour while letting the derived client own its identity hook.
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=verify_ssl,
            headers=headers,
            event_hooks=identity_hooks,
        )

        # Initialize MAP HTTP client with longer timeout
        self._map_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(map_timeout),
            verify=verify_ssl,
            headers=headers,
            event_hooks=identity_hooks,
        )

        # Initialize cache
        self._cache: TTLCache[str, ClientResponse] | None = None
        if cache_enabled:
            self._cache = TTLCache(maxsize=cache_max_size, ttl=cache_ttl)

        # Initialize logger
        self._logger = structlog.get_logger(__name__).bind(
            client_id=client_id or "community",
            mode=mode.value,
        )

        # Initialize session cookie for portal authentication
        self._session_cookie: str | None = None

        # Initialize MAS FEAT namespace (lazy)
        self._masfeat: MASFEATNamespace | None = None

        if debug:
            self._logger.info(
                "AxonFlow client initialized",
                endpoint=endpoint,
            )

        # Heartbeat gate: at most one anonymous ping per environment per
        # 7 days, gated by SDK activity. The constructor runs the gate
        # synchronously enough to schedule the daemon thread; the thread
        # is tracked by an atexit flush handler so short-lived processes
        # (CLI, serverless cold-starts) still deliver the ping. Subsequent
        # gate runs happen async via ``_pre_request_hook`` on every
        # public HTTP request. See axonflow/heartbeat.py for the contract
        # and stamp-on-DELIVERY semantics. The v7.x ``telemetry_enabled``
        # programmatic override was removed in v8.0; AXONFLOW_TELEMETRY=off
        # is now the sole opt-out lever.
        maybe_send_heartbeat(
            mode=self._config.mode.value,
            endpoint=self._config.endpoint,
            debug=debug,
        )

    @property
    def masfeat(self) -> MASFEATNamespace:
        """MAS FEAT compliance methods namespace.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Provides access to MAS FEAT compliance methods:
        - Registry: register_system, get_system, update_system, list_systems, etc.
        - Assessments: create_assessment, update_assessment, approve_assessment, etc.
        - Kill Switch: configure_kill_switch, check_kill_switch, trigger_kill_switch, etc.

        Example:
            >>> async with AxonFlow(endpoint="...") as client:
            ...     # Register an AI system
            ...     system = await client.masfeat.register_system(
            ...         system_id="credit-scoring-v1",
            ...         system_name="Credit Scoring AI",
            ...         use_case="credit_scoring",
            ...         owner_team="Risk Management",
            ...         customer_impact=4,
            ...         model_complexity=3,
            ...         human_reliance=5,
            ...     )
            ...     print(system.materiality)  # 'high' (sum=12)
            ...
            ...     # Configure kill switch
            ...     ks = await client.masfeat.configure_kill_switch(
            ...         "credit-scoring-v1",
            ...         accuracy_threshold=0.85,
            ...         bias_threshold=0.15,
            ...         auto_trigger_enabled=True,
            ...     )
        """
        if self._masfeat is None:
            self._masfeat = MASFEATNamespace(self)
        return self._masfeat

    @property
    def config(self) -> AxonFlowConfig:
        """Get client configuration."""
        return self._config

    def _has_credentials(self) -> bool:
        """Check if credentials are configured.

        Returns True if client_id is set.
        client_secret is optional for community mode but required for enterprise.
        """
        return bool(self._config.client_id)

    def _get_effective_client_id(self) -> str:
        """Get the effective client_id, using smart default for community mode.

        Returns the configured client_id if set, otherwise returns "community"
        as a smart default. This enables zero-config usage for community/self-hosted
        deployments while still supporting enterprise deployments with explicit credentials.

        Returns:
            The client_id to use in requests
        """
        return self._config.client_id if self._config.client_id else "community"

    async def __aenter__(self) -> AxonFlow:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP clients."""
        await self._http_client.aclose()
        await self._map_http_client.aclose()

    @classmethod
    def sync(
        cls,
        endpoint: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> SyncAxonFlow:
        """Create a synchronous client wrapper.

        Example:
            >>> # Enterprise mode with authentication
            >>> client = AxonFlow.sync(endpoint="...", client_id="...", client_secret="...")
            >>> result = client.proxy_llm_call("token", "query", "chat")
            >>>
            >>> # Community/self-hosted mode (no auth required)
            >>> client = AxonFlow.sync(endpoint="http://localhost:8080")
            >>> result = client.proxy_llm_call("token", "query", "chat")
        """
        return SyncAxonFlow(cls(endpoint, client_id, client_secret, **kwargs))

    @classmethod
    def sandbox(
        cls,
        client_id: str = "demo-client",  # noqa: S107
        client_secret: str = "demo-secret",  # noqa: S107
    ) -> AxonFlow:
        """Create a sandbox client targeting a local community stack.

        Assumes a local docker-compose community stack is running at
        ``http://localhost:8080`` (agent) / ``http://localhost:8081`` (orchestrator).
        Start the stack with ``docker compose up`` from
        https://github.com/getaxonflow/axonflow before calling this.

        For the hosted community sandbox at https://try.getaxonflow.com, use
        the registration flow instead: call ``POST /api/v1/register`` to mint
        tenant credentials, then construct an ``AxonFlow(...)`` client directly
        or set ``AXONFLOW_TRY=1``.

        Args:
            client_id: Optional client ID (defaults to demo-client)
            client_secret: Optional client secret (defaults to demo-secret)

        Returns:
            Configured AxonFlow client for a local sandbox environment
        """
        return cls(
            endpoint="http://localhost:8080",
            client_id=client_id,
            client_secret=client_secret,
            mode=Mode.SANDBOX,
            debug=True,
        )

    def _get_cache_key(self, request_type: str, query: str, user_token: str) -> str:
        """Generate cache key for a request."""
        key = f"{request_type}:{query}:{user_token}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def _pre_request_hook(self) -> None:
        """Single hook invoked at the start of every public HTTP request path.

        Schedules a heartbeat-gate evaluation via ``maybe_send_heartbeat``.
        The gate spawns its own daemon thread for any actual POST, so this
        method is non-blocking — user API calls are never delayed by
        telemetry. The gate's in-memory 1-hour cache plus the in-flight
        flag mean the typical hot-path cost is a single mutex acquire and
        a monotonic-time read.
        """
        maybe_send_heartbeat(
            mode=self._config.mode.value,
            endpoint=self._config.endpoint,
            debug=self._config.debug,
        )

    async def _send_raw(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Transport only: send the request and hand back whatever came out.

        This is the half of ``_request`` that has nothing to do with what a
        status code MEANS — the heartbeat gate, the URL, the retry policy and
        the transport-level failure mapping. It is extracted rather than copied
        because a caller that needs the raw status (the AuthZEN surface, whose
        refusals are typed documents on 4xx) would otherwise stand up a second
        HTTP path, and a second path is a second place for credentials,
        timeouts, proxy configuration and telemetry to drift out of step with
        the client the user configured.

        ``_request`` keeps every status-code behaviour it had; only the send
        moved.
        """
        self._pre_request_hook()
        url = f"{self._config.endpoint}{path}"

        try:
            if self._config.retry.enabled:
                return await self._request_with_retry(method, url, json_data, headers=headers)
            return await self._http_client.request(method, url, json=json_data, headers=headers)
        except httpx.ConnectError as e:
            msg = f"Failed to connect to AxonFlow Agent: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Agent."""
        response = await self._send_raw(method, path, json_data=json_data)

        try:
            response.raise_for_status()
            # Handle 204 No Content (e.g., DELETE responses)
            if response.status_code == 204:  # noqa: PLR2004
                return None  # type: ignore[return-value]
            return response.json()  # type: ignore[no-any-return]

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:  # noqa: PLR2004
                msg = "Invalid credentials"
                raise AuthenticationError(msg) from e
            if e.response.status_code == 402:  # noqa: PLR2004
                body = e.response.json()
                budget_info = body.get("budget_info", {})
                raise BudgetExceededError(
                    body.get("message", "Budget exceeded"),
                    budget_id=budget_info.get("budget_id"),
                    budget_name=budget_info.get("budget_name"),
                    used_usd=budget_info.get("used_usd", 0.0),
                    limit_usd=budget_info.get("limit_usd", 0.0),
                    action=budget_info.get("action"),
                ) from e
            if e.response.status_code == 403:  # noqa: PLR2004
                body = e.response.json()
                # Extract policy from policy_info if available
                policy = body.get("policy")
                if not policy:
                    policy_info = body.get("policy_info")
                    if policy_info and policy_info.get("policies_evaluated"):
                        policy = policy_info["policies_evaluated"][0]
                raise PolicyViolationError(
                    body.get("block_reason") or body.get("message", "Request blocked by policy"),
                    policy=policy,
                    block_reason=body.get("block_reason"),
                ) from e
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        json_data: dict[str, Any] | None,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make request with retry logic.

        ``headers`` are merged over the client's own by httpx. It is
        keyword-only with a default so every existing call site is unchanged;
        it exists because the AuthZEN surface has to negotiate a profile on a
        per-request header and must not do so through a second HTTP client.
        """

        @retry(
            stop=stop_after_attempt(self._config.retry.max_attempts),
            wait=wait_exponential(
                multiplier=self._config.retry.initial_delay,
                max=self._config.retry.max_delay,
                exp_base=self._config.retry.exponential_base,
            ),
            retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
            reraise=True,
        )
        async def _do_request() -> httpx.Response:
            return await self._http_client.request(method, url, json=json_data, headers=headers)

        return await _do_request()

    async def _map_request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Agent using MAP timeout.

        This uses the longer map_timeout for MAP operations that involve
        multiple LLM calls and can take 30-60+ seconds.
        """
        self._pre_request_hook()
        url = f"{self._config.endpoint}{path}"

        try:
            if self._config.debug:
                self._logger.debug(
                    "MAP request",
                    url=url,
                    timeout=self._config.map_timeout,
                )

            response = await self._map_http_client.request(method, url, json=json_data)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

        except httpx.ConnectError as e:
            msg = f"Failed to connect to AxonFlow Agent: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"MAP request timed out after {self._config.map_timeout}s: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:  # noqa: PLR2004
                msg = "Invalid credentials"
                raise AuthenticationError(msg) from e
            if e.response.status_code == 402:  # noqa: PLR2004
                body = e.response.json()
                budget_info = body.get("budget_info", {})
                raise BudgetExceededError(
                    body.get("message", "Budget exceeded"),
                    budget_id=budget_info.get("budget_id"),
                    budget_name=budget_info.get("budget_name"),
                    used_usd=budget_info.get("used_usd", 0.0),
                    limit_usd=budget_info.get("limit_usd", 0.0),
                    action=budget_info.get("action"),
                ) from e
            if e.response.status_code == 403:  # noqa: PLR2004
                body = e.response.json()
                policy = body.get("policy")
                if not policy:
                    policy_info = body.get("policy_info")
                    if policy_info and policy_info.get("policies_evaluated"):
                        policy = policy_info["policies_evaluated"][0]
                raise PolicyViolationError(
                    body.get("block_reason") or body.get("message", "Request blocked by policy"),
                    policy=policy,
                    block_reason=body.get("block_reason"),
                ) from e
            if e.response.status_code == 409:  # noqa: PLR2004
                body = e.response.json()
                raise VersionConflictError(
                    plan_id=body.get("plan_id", ""),
                    expected_version=body.get("expected_version", 0),
                    current_version=body.get("current_version"),
                ) from e
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

    async def health_check(self) -> bool:
        """Check if AxonFlow Agent is healthy.

        Returns:
            True if agent is healthy, False otherwise
        """
        try:
            response = await self._request("GET", "/health")
            return response.get("status") == "healthy"
        except AxonFlowError:
            return False

    async def health_check_detailed(self) -> HealthResponse:
        """Get detailed health info including capabilities and version.

        Returns:
            HealthResponse with platform version, capabilities, and SDK compatibility.

        Raises:
            AxonFlowError: If the health check request fails.
        """
        response = await self._request("GET", "/health")
        caps = [
            PlatformCapability(
                name=c.get("name", ""),
                since=c.get("since", ""),
                description=c.get("description", ""),
            )
            for c in response.get("capabilities", [])
        ]
        compat_data = response.get("sdk_compatibility")
        compat = None
        if compat_data:
            raw_min = compat_data.get("min_sdk_version", {})
            raw_recommended = compat_data.get("recommended_sdk_version", {})
            # Defensive: accept legacy bare-string shape from old platforms.
            if isinstance(raw_min, str):
                raw_min = {"python": raw_min} if raw_min else {}
            if isinstance(raw_recommended, str):
                raw_recommended = {"python": raw_recommended} if raw_recommended else {}
            compat = SDKCompatibility(
                min_sdk_version=raw_min,
                recommended_sdk_version=raw_recommended,
            )
        health = HealthResponse(
            status=response.get("status", "unknown"),
            service=response.get("service", ""),
            version=response.get("version", ""),
            capabilities=caps,
            sdk_compatibility=compat,
        )
        if compat:
            min_for_python = compat.min_sdk_version_for("python")
            if min_for_python and _parse_version(_SDK_VERSION) < _parse_version(min_for_python):
                logging.getLogger("axonflow").warning(
                    "SDK version %s is below minimum supported version %s. Please upgrade.",
                    _SDK_VERSION,
                    min_for_python,
                )
        return health

    async def orchestrator_health_check(self) -> bool:
        """Check if AxonFlow Orchestrator is healthy.

        Returns:
            True if orchestrator is healthy, False otherwise
        """
        try:
            response = await self._orchestrator_request("GET", "/health")
        except AxonFlowError:
            return False
        else:
            if isinstance(response, dict):
                return response.get("status") == "healthy"
            return False

    async def proxy_llm_call(
        self,
        user_token: str,
        query: str,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Send a query through AxonFlow with full policy enforcement (Proxy Mode).

        This is Proxy Mode - AxonFlow acts as an intermediary, making the LLM call
        on your behalf.

        Use this when you want AxonFlow to:
          - Evaluate policies before the LLM call
          - Make the LLM call to the configured provider
          - Filter/redact sensitive data from responses
          - Automatically track costs and audit the interaction

        For Gateway Mode (lower latency, you make the LLM call), use:
          - get_policy_approved_context() before your LLM call
          - audit_llm_call() after your LLM call

        Args:
            user_token: User authentication token. If empty, defaults to "anonymous"
                for audit purposes (community mode).
            query: The query or prompt
            request_type: Type of request (chat, sql, mcp-query, multi-agent-plan)
            context: Optional additional context

        Returns:
            ClientResponse with results or error

        Raises:
            PolicyViolationError: If request is blocked by policy
            AuthenticationError: If credentials are invalid
            TimeoutError: If request times out
        """
        # Default to "anonymous" if user_token is empty (community mode)
        if not user_token:
            user_token = "anonymous"  # noqa: S105 - not a password, just a placeholder

        # Plan operations are mutations and must not be cached
        is_mutation = request_type in (
            "execute-plan",
            "generate-plan",
            "cancel-plan",
            "update-plan",
        )

        # Check cache (skip for mutations)
        if self._cache is not None and not is_mutation:
            cache_key = self._get_cache_key(request_type, query, user_token)
            if cache_key in self._cache:
                if self._config.debug:
                    self._logger.debug("Cache hit", query=query[:50])
                cached_result: ClientResponse = self._cache[cache_key]
                return cached_result
        else:
            cache_key = ""

        request = ClientRequest(
            query=query,
            user_token=user_token,
            client_id=self._config.client_id,
            request_type=request_type,
            context=context or {},
        )

        if self._config.debug:
            self._logger.debug(
                "Executing query",
                request_type=request_type,
                query=query[:50] if query else "",
            )

        response_data = await self._request(
            "POST",
            "/api/request",
            json_data=request.model_dump(),
        )

        response = ClientResponse.model_validate(response_data)

        # Check for policy violation
        if response.blocked:
            # Extract policy name from policy_info if available
            policy = None
            if response.policy_info and response.policy_info.policies_evaluated:
                policy = response.policy_info.policies_evaluated[0]
            raise PolicyViolationError(
                response.block_reason or "Request blocked by policy",
                policy=policy,
                block_reason=response.block_reason,
            )

        # Cache successful responses (skip mutations — plan operations)
        if self._cache is not None and response.success and cache_key and not is_mutation:
            self._cache[cache_key] = response

        return response

    async def proxy_llm_call_with_media(
        self,
        user_token: str,
        query: str,
        request_type: str,
        media: list[MediaContent],
        context: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Send a request with media content (images) for governance analysis.

        Media items are analyzed for PII, content safety, biometric data, and
        document classification before being forwarded to the LLM provider.

        Args:
            user_token: User authentication token.
            query: The prompt/query text.
            request_type: Type of request (e.g., "chat", "sql").
            media: List of MediaContent items (images) to analyze.
            context: Optional additional context.

        Returns:
            ClientResponse with media_analysis field populated.

        Raises:
            PolicyViolationError: If request is blocked by policy
            AuthenticationError: If credentials are invalid
            TimeoutError: If request times out
        """
        # Default to "anonymous" if user_token is empty (community mode)
        if not user_token:
            user_token = "anonymous"  # noqa: S105 - not a password, just a placeholder

        # Media requests skip cache: analysis is non-deterministic and
        # cache keys don't incorporate binary image data.
        request = ClientRequest(
            query=query,
            user_token=user_token,
            client_id=self._config.client_id,
            request_type=request_type,
            context=context or {},
            media=media,
        )

        if self._config.debug:
            self._logger.debug(
                "Executing multimodal query",
                request_type=request_type,
                query=query[:50] if query else "",
                media_count=len(media),
            )

        response_data = await self._request(
            "POST",
            "/api/request",
            json_data=request.model_dump(),
        )

        response = ClientResponse.model_validate(response_data)

        # Check for policy violation
        if response.blocked:
            # Extract policy name from policy_info if available
            policy = None
            if response.policy_info and response.policy_info.policies_evaluated:
                policy = response.policy_info.policies_evaluated[0]
            raise PolicyViolationError(
                response.block_reason or "Request blocked by policy",
                policy=policy,
                block_reason=response.block_reason,
            )

        # Media requests are never cached (cache_key is always empty above).

        return response

    async def list_connectors(self) -> list[ConnectorMetadata]:
        """List all available MCP connectors.

        Returns:
            List of connector metadata
        """
        response = await self._orchestrator_request("GET", "/api/v1/connectors")
        # Response is wrapped: {"connectors": [...], "total": N}
        if isinstance(response, dict) and "connectors" in response:
            return [ConnectorMetadata.model_validate(c) for c in response["connectors"]]
        # Fallback for direct list response
        return [ConnectorMetadata.model_validate(c) for c in response or []]

    async def install_connector(self, request: ConnectorInstallRequest) -> None:
        """Install an MCP connector.

        Args:
            request: Connector installation request
        """
        await self._orchestrator_request(
            "POST",
            f"/api/v1/connectors/{request.connector_id}/install",
            json_data=request.model_dump(exclude={"connector_id"}),
        )

        if self._config.debug:
            self._logger.info("Connector installed", name=request.name)

    async def uninstall_connector(self, connector_name: str) -> None:
        """Uninstall an MCP connector.

        Args:
            connector_name: Name of the connector to uninstall
        """
        await self._orchestrator_request(
            "DELETE",
            f"/api/v1/connectors/{connector_name}",
        )

        if self._config.debug:
            self._logger.info("Connector uninstalled", name=connector_name)

    async def get_connector(self, connector_id: str) -> ConnectorMetadata:
        """Get details for a specific connector.

        Args:
            connector_id: ID of the connector

        Returns:
            Connector metadata

        Raises:
            AxonFlowError: If connector not found
        """
        response = await self._orchestrator_request(
            "GET",
            f"/api/v1/connectors/{connector_id}",
        )

        if self._config.debug:
            self._logger.info("Got connector", id=connector_id)

        return ConnectorMetadata.model_validate(response)

    async def get_connector_health(self, connector_id: str) -> ConnectorHealthStatus:
        """Get health status of an installed connector.

        Args:
            connector_id: ID of the connector

        Returns:
            Connector health status

        Raises:
            AxonFlowError: If connector not found or not installed
        """
        response = await self._orchestrator_request(
            "GET",
            f"/api/v1/connectors/{connector_id}/health",
        )

        if self._config.debug and isinstance(response, dict):
            self._logger.info("Connector health", id=connector_id, healthy=response.get("healthy"))

        return ConnectorHealthStatus.model_validate(response)

    async def query_connector(
        self,
        user_token: str,
        connector_name: str,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Query an MCP connector directly.

        Args:
            user_token: User authentication token
            connector_name: Name of the connector
            operation: Operation to perform (e.g., Slack API method like "conversations.list")
            params: Operation parameters

        Returns:
            ConnectorResponse with results
        """
        # Use the standard /api/request endpoint with request_type="mcp-query"
        # This ensures proper authentication and license validation flow
        context = {
            "connector": connector_name,
            "params": params or {},
        }

        # Execute via the standard request flow
        client_response = await self.proxy_llm_call(
            user_token=user_token,
            query=operation,
            request_type="mcp-query",
            context=context,
        )

        # Map ClientResponse to ConnectorResponse
        policy_info = {}
        if client_response.policy_info:
            policy_info = client_response.policy_info.model_dump()

        return ConnectorResponse(
            success=client_response.success,
            data=client_response.data,
            error=client_response.error,
            meta={
                "blocked": client_response.blocked,
                "block_reason": client_response.block_reason,
                "policy_info": policy_info,
            },
        )

    async def mcp_query(
        self,
        connector: str,
        statement: str,
        options: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Execute a query directly against the MCP connector endpoint.

        This method calls the agent's /mcp/resources/query endpoint which provides:
        - Request-phase policy evaluation (SQLi blocking, PII blocking)
        - Response-phase policy evaluation (PII redaction)
        - PolicyInfo metadata in responses

        Args:
            connector: Name of the MCP connector (e.g., "postgres")
            statement: SQL statement or query to execute
            options: Optional additional options for the query

        Returns:
            ConnectorResponse with data, redaction info, and policy_info.
            When blocked by policy (HTTP 403), returns blocked=True with block_reason.

        Raises:
            ConnectorError: If the request fails (non-403 errors only)

        Example:
            response = await client.mcp_query(
                connector="postgres",
                statement="SELECT * FROM customers LIMIT 10"
            )
            if response.redacted:
                print(f"Redacted fields: {response.redacted_fields}")
        """
        if not connector:
            msg = "connector name is required"
            raise ConnectorError(msg, connector=None, operation="mcp_query")
        if not statement:
            msg = "statement is required"
            raise ConnectorError(msg, connector=connector, operation="mcp_query")

        url = f"{self._config.endpoint}/mcp/resources/query"
        body = {
            "connector": connector,
            "statement": statement,
            "options": options or {},
        }

        if self._config.debug:
            self._logger.debug("MCP Query", connector=connector, statement=statement[:50])

        response = await self._http_client.post(url, json=body)
        response_data = response.json()

        # All 403 responses from mcp_query are policy blocks.
        # Auth errors return 401. Static policy blocks return 403 without policy_info,
        # dynamic policy blocks return 403 with policy_info. Both are policy decisions.
        is_policy_block = (
            response.status_code == 403  # noqa: PLR2004
            and isinstance(response_data, dict)
        )

        if not response.is_success and not is_policy_block:
            error_msg = response_data.get("error", f"MCP query failed: {response.status_code}")
            raise ConnectorError(error_msg, connector=connector, operation="mcp_query")

        if self._config.debug:
            self._logger.debug(
                "MCP Query result",
                connector=connector,
                success=response_data.get("success"),
                redacted=response_data.get("redacted"),
            )

        # Build policy_info if present
        policy_info = None
        if response_data.get("policy_info"):
            policy_info = ConnectorPolicyInfo.model_validate(response_data["policy_info"])

        return ConnectorResponse(
            success=response_data.get("success", not is_policy_block),
            data=response_data.get("data"),
            error=response_data.get("error"),
            meta=response_data.get("meta", {}),
            redacted=response_data.get("redacted", False),
            redacted_fields=response_data.get("redacted_fields", []),
            blocked=is_policy_block,
            block_reason=response_data.get("error") if is_policy_block else None,
            policy_info=policy_info,
        )

    async def mcp_execute(
        self,
        connector: str,
        statement: str,
        options: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Execute a statement against an MCP connector (alias for mcp_query).

        Same as mcp_query but follows the naming convention of other execute* methods.
        """
        return await self.mcp_query(connector, statement, options)

    async def mcp_check_input(
        self,
        connector_type: str,
        statement: str,
        operation: str = "execute",
        parameters: dict[str, Any] | None = None,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        user_token: str | None = None,
        content_type: str | None = None,
    ) -> MCPCheckInputResponse:
        """Validate an MCP request against configured policies without executing it.

        Use this when an external orchestrator (e.g., LangGraph, CrewAI) manages MCP
        execution but needs AxonFlow policy enforcement as a pre-execution gate.

        Args:
            connector_type: Type of MCP connector (e.g., "postgres", "snowflake").
            statement: The SQL query or command to validate.
            operation: Operation type - "query" or "execute" (default).
            parameters: Optional query parameters.
            tool: Optional tool name, distinct from ``connector_type``. Lets a
                PEP report the (server, tool) identity as two separate values
                instead of concatenating them into ``connector_type``
                (epic #2905 / #2904).
            client_id: Client identifier (overrides client config when set).
            tenant_id: Tenant identifier for multi-tenant scoping.
            user_id: End-user identifier for per-user policies.
            user_role: User role for role-based policies.
            user_token: User auth token for downstream propagation.
            content_type: Selects the request-redaction detector (ADR-056 /
                #2563). ``None`` defaults to "text/plain" server-side. Used by
                a PEP fulfilling a ``redact_pii`` obligation.

        Returns:
            MCPCheckInputResponse with allowed status, block reason, and policy info.
            When the statement carries PII under a redact policy, the engine
            returns ``redacted_statement`` so a PEP forwards engine-redacted
            content without hand-rolling its own patterns.

        Raises:
            ConnectorError: If the request fails (non-403 errors only).
        """
        url = f"{self._config.endpoint}/api/v1/mcp/check-input"
        body: dict[str, Any] = {
            "connector_type": connector_type,
            "statement": statement,
            "operation": operation,
        }
        if parameters:
            body["parameters"] = parameters
        if tool:
            body["tool"] = tool
        # Wire-canonical scoping fields surfaced in the v6 sweep.
        if client_id is not None:
            body["client_id"] = client_id
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        if user_id is not None:
            body["user_id"] = user_id
        if user_role is not None:
            body["user_role"] = user_role
        if user_token is not None:
            body["user_token"] = user_token
        if content_type is not None:
            body["content_type"] = content_type

        if self._config.debug:
            self._logger.debug(
                "MCP check-input",
                connector_type=connector_type,
                statement=statement[:50],
            )

        response = await self._http_client.post(url, json=body)
        data = response.json()

        if not response.is_success and response.status_code != 403:  # noqa: PLR2004
            error_msg = data.get("error", "MCP check-input failed")
            raise ConnectorError(error_msg, connector_type, "check-input")

        return MCPCheckInputResponse(**data)

    async def check_tool_input(
        self,
        connector_type: str,
        statement: str,
        operation: str = "execute",
        parameters: dict[str, Any] | None = None,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckInputResponse:
        """Alias for :meth:`mcp_check_input`. Validates tool input against configured policies."""
        return await self.mcp_check_input(
            connector_type,
            statement,
            operation,
            parameters,
            tool=tool,
            client_id=client_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_role=user_role,
            user_token=user_token,
        )

    async def mcp_check_output(
        self,
        connector_type: str,
        response_data: list[dict[str, Any]] | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        row_count: int = 0,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckOutputResponse:
        """Validate MCP response data against configured policies.

        Use this when an external orchestrator manages MCP execution but needs AxonFlow
        policy enforcement as a post-execution gate (PII redaction, exfiltration limits).

        Args:
            connector_type: Type of MCP connector (e.g., "postgres", "snowflake").
            response_data: Array of row objects from a query response.
            message: Execute-style response message (e.g., "5 rows affected").
            metadata: Connector metadata for SQLi scanning.
            row_count: Total number of rows returned.
            tool: Optional tool name, distinct from ``connector_type``. Mirrors
                the ``tool`` parameter on :meth:`mcp_check_input` (epic #2905 /
                #2904).
            client_id: Client identifier (overrides client config when set).
            tenant_id: Tenant identifier for multi-tenant scoping.
            user_id: End-user identifier for per-user policies.
            user_token: User auth token for downstream propagation.

        Returns:
            MCPCheckOutputResponse with allowed status, redacted data, and policy info.

        Raises:
            ConnectorError: If the request fails (non-403 errors only).
        """
        url = f"{self._config.endpoint}/api/v1/mcp/check-output"
        body: dict[str, Any] = {
            "connector_type": connector_type,
        }
        if response_data is not None:
            body["response_data"] = response_data
        if message is not None:
            body["message"] = message
        if metadata is not None:
            body["metadata"] = metadata
        if row_count > 0:
            body["row_count"] = row_count
        if tool:
            body["tool"] = tool
        # Wire-canonical scoping fields surfaced in the v6 sweep.
        if client_id is not None:
            body["client_id"] = client_id
        if tenant_id is not None:
            body["tenant_id"] = tenant_id
        if user_id is not None:
            body["user_id"] = user_id
        if user_token is not None:
            body["user_token"] = user_token

        if self._config.debug:
            self._logger.debug(
                "MCP check-output",
                connector_type=connector_type,
                row_count=row_count,
            )

        response = await self._http_client.post(url, json=body)
        data = response.json()

        if not response.is_success and response.status_code != 403:  # noqa: PLR2004
            error_msg = data.get("error", "MCP check-output failed")
            raise ConnectorError(error_msg, connector_type, "check-output")

        return MCPCheckOutputResponse(**data)

    async def check_tool_output(
        self,
        connector_type: str,
        response_data: list[dict[str, Any]] | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        row_count: int = 0,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckOutputResponse:
        """Alias for :meth:`mcp_check_output`. Validates tool output against configured policies."""
        return await self.mcp_check_output(
            connector_type,
            response_data,
            message,
            metadata,
            row_count,
            tool=tool,
            client_id=client_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_token=user_token,
        )

    async def generate_plan(
        self,
        query: str,
        domain: str | None = None,
        user_token: str | None = None,
        execution_mode: ExecutionMode | None = None,
    ) -> PlanResponse:
        """Generate a multi-agent execution plan.

        Args:
            query: Natural language query describing the task
            domain: Optional domain hint (travel, healthcare, etc.)
            user_token: Optional user token for authentication (defaults to client_id)
            execution_mode: Optional execution mode (auto, sequential, parallel, etc.)

        Returns:
            PlanResponse with generated plan

        Note:
            This uses map_timeout (default 120s) as MAP operations involve
            multiple LLM calls and can take 30-60+ seconds.
        """
        context: dict[str, Any] = {}
        if domain:
            context["domain"] = domain
        if execution_mode is not None:
            context["execution_mode"] = execution_mode.value

        request = ClientRequest(
            query=query,
            user_token=user_token or self._config.client_id or "",
            client_id=self._config.client_id,
            request_type="multi-agent-plan",
            context=context,
        )

        if self._config.debug:
            self._logger.debug(
                "Generating plan",
                query=query[:50] if query else "",
                domain=domain,
                timeout=self._config.map_timeout,
            )

        # Use MAP request with longer timeout
        response_data = await self._map_request(
            "POST",
            "/api/request",
            json_data=request.model_dump(),
        )

        response = ClientResponse.model_validate(response_data)

        if not response.success:
            msg = f"Plan generation failed: {response.error}"
            raise AxonFlowError(msg)

        # Extract steps from response data
        steps: list[PlanStep] = []
        if response.data and isinstance(response.data, dict):
            steps_data = response.data.get("steps", [])
            steps = [PlanStep.model_validate(s) for s in steps_data]
            # Also check for plan_id in data
            if not response.plan_id and response.data.get("plan_id"):
                response = ClientResponse.model_validate(
                    {
                        **response_data,
                        "plan_id": response.data.get("plan_id"),
                    }
                )

        plan_id = response.plan_id or (
            response.data.get("plan_id", "") if isinstance(response.data, dict) else ""
        )
        # Source the wire top-level fields (success / version / result /
        # error / workflow_execution_id / policy_info) from the parsed
        # ClientResponse + nested data dict. They appear at the top
        # level on current platform builds; older nested-only builds
        # used `data.*`, so fall back through both layers.
        data_dict: dict[str, Any] = response.data if isinstance(response.data, dict) else {}
        # `policy_info` on PlanResponse is the spec's PolicyEvaluationResult
        # shape (different from ClientResponse.policy_info, which is
        # PolicyEvaluationInfo). Source from the wire dict and validate
        # against the typed model so callers see the right shape.
        wire_policy_info_raw = data_dict.get("policy_info")
        policy_info_typed: PolicyEvaluationResult | None = (
            PolicyEvaluationResult.model_validate(wire_policy_info_raw)
            if isinstance(wire_policy_info_raw, dict)
            else None
        )
        # Use `is not None` rather than `or` to fall through — `or` would
        # falsey-clobber legitimate empty results (0, False, "", [], {})
        # with the data_dict fallback.
        result_value = response.result if response.result is not None else data_dict.get("result")
        return PlanResponse(
            plan_id=plan_id,
            steps=steps,
            domain=data_dict.get("domain", domain or "generic"),
            complexity=data_dict.get("complexity", 0),
            parallel=data_dict.get("parallel", False),
            metadata=response.metadata,
            success=response.success,
            version=data_dict.get("version"),
            result=result_value,
            error=response.error,
            workflow_execution_id=data_dict.get("workflow_execution_id"),
            policy_info=policy_info_typed,
        )

    async def execute_plan(
        self,
        plan_id: str,
        user_token: str | None = None,
    ) -> PlanExecutionResponse:
        """Execute a previously generated plan.

        Args:
            plan_id: ID of the plan to execute
            user_token: Optional user token for authentication (defaults to client_id)

        Returns:
            PlanExecutionResponse with results

        Note:
            This uses map_timeout (default 120s) as plan execution involves
            multiple LLM calls and can take 30-60+ seconds.
        """
        request = ClientRequest(
            query="",
            user_token=user_token or self._config.client_id or "",
            client_id=self._config.client_id,
            request_type="execute-plan",
            context={"plan_id": plan_id},
        )

        if self._config.debug:
            self._logger.debug(
                "Executing plan",
                plan_id=plan_id,
                timeout=self._config.map_timeout,
            )

        # Use MAP request with longer timeout
        response_data = await self._map_request(
            "POST",
            "/api/request",
            json_data=request.model_dump(),
        )

        response = ClientResponse.model_validate(response_data)

        # Check for nested failure: server returns HTTP 200 with data.success=false
        # (e.g., "Plan has been cancelled") — the outer success may still be true
        if (
            response.data
            and isinstance(response.data, dict)
            and response.data.get("success") is False
        ):
            error_msg = response.data.get("error", "Plan execution failed")
            raise PlanExecutionError(
                message=error_msg,
                plan_id=plan_id,
            )

        # Determine status from response data (e.g., "awaiting_approval" for confirm mode)
        # Priority: data.status > metadata.status > success-based default
        status = None
        workflow_id = None
        if response.data and isinstance(response.data, dict):
            status = response.data.get("status")
            if wf_id := response.data.get("workflow_id"):
                workflow_id = wf_id
        if not status and response.metadata:
            status = response.metadata.get("status")
        if not status:
            status = "completed" if response.success else "failed"

        return PlanExecutionResponse(
            plan_id=plan_id,
            status=status,
            workflow_id=workflow_id,
            result=response.result,
            step_results=response.metadata.get("step_results", {}),
            error=response.error,
            duration=response.metadata.get("duration"),
        )

    async def get_plan_status(self, plan_id: str) -> PlanExecutionResponse:
        """Get status of a running or completed plan.

        Args:
            plan_id: ID of the plan

        Returns:
            PlanExecutionResponse with current status
        """
        response = await self._request("GET", f"/api/v1/plan/{plan_id}")
        return PlanExecutionResponse.model_validate(response)

    async def cancel_plan(
        self,
        plan_id: str,
        reason: str | None = None,
    ) -> CancelPlanResponse:
        """Cancel a running plan.

        Args:
            plan_id: ID of the plan to cancel
            reason: Optional reason for cancellation

        Returns:
            CancelPlanResponse with cancellation confirmation
        """
        json_data: dict[str, Any] = {}
        if reason is not None:
            json_data["reason"] = reason

        response = await self._map_request(
            "POST",
            f"/api/v1/plan/{plan_id}/cancel",
            json_data=json_data if json_data else None,
        )
        return CancelPlanResponse.model_validate(response)

    async def update_plan(
        self,
        plan_id: str,
        request: UpdatePlanRequest,
    ) -> UpdatePlanResponse:
        """Update a plan with optimistic concurrency control.

        Args:
            plan_id: ID of the plan to update
            request: Update request with expected_version for optimistic locking

        Returns:
            UpdatePlanResponse with updated plan info

        Raises:
            VersionConflictError: If the plan was modified since expected_version
        """
        json_data: dict[str, Any] = {"version": request.expected_version}
        if request.execution_mode is not None:
            json_data["execution_mode"] = request.execution_mode.value
        if request.domain is not None:
            json_data["domain"] = request.domain
        if request.metadata is not None:
            json_data["metadata"] = request.metadata

        response = await self._map_request(
            "PUT",
            f"/api/v1/plan/{plan_id}",
            json_data=json_data,
        )
        return UpdatePlanResponse.model_validate(response)

    async def get_plan_versions(self, plan_id: str) -> PlanVersionsResponse:
        """Get version history of a plan.

        Args:
            plan_id: ID of the plan

        Returns:
            PlanVersionsResponse with version history entries
        """
        response = await self._request("GET", f"/api/v1/plan/{plan_id}/versions")
        return PlanVersionsResponse.model_validate(response)

    async def resume_plan(
        self,
        plan_id: str,
        approved: bool | None = None,
    ) -> ResumePlanResponse:
        """Resume a paused plan.

        Args:
            plan_id: ID of the plan to resume
            approved: Whether the resume is approved (defaults to True if None)

        Returns:
            ResumePlanResponse with resume confirmation
        """
        json_data: dict[str, Any] = {
            "approved": approved if approved is not None else True,
        }

        response = await self._map_request(
            "POST",
            f"/api/v1/plan/{plan_id}/resume",
            json_data=json_data,
        )
        return ResumePlanResponse.model_validate(response)

    # =========================================================================
    # Gateway Mode Methods
    # =========================================================================

    async def get_policy_approved_context(
        self,
        user_token: str,
        query: str,
        data_sources: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyApprovalResult:
        """Perform policy pre-check before making LLM call.

        This is the first step in Gateway Mode. Call this before making your
        LLM call to ensure policy compliance.

        Note:
            Uses smart default "community" for client_id if not configured,
            enabling zero-config usage for community/self-hosted deployments.

        Args:
            user_token: JWT token for the user making the request
            query: The query/prompt that will be sent to the LLM
            data_sources: Optional list of MCP connectors to fetch data from
            context: Optional additional context for policy evaluation

        Returns:
            PolicyApprovalResult with context ID and approved data

        Raises:
            AuthenticationError: If user token is invalid
            ConnectionError: If unable to reach AxonFlow Agent
            TimeoutError: If request times out

        Example:
            >>> result = await client.get_policy_approved_context(
            ...     user_token="user-jwt",
            ...     query="Find patients with diabetes",
            ...     data_sources=["postgres"]
            ... )
            >>> if not result.approved:
            ...     raise PolicyViolationError(result.block_reason)
        """
        # Use smart default for client_id - enables zero-config community mode
        client_id = self._get_effective_client_id()

        request_body = {
            "user_token": user_token,
            "client_id": client_id,
            "query": query,
            "data_sources": data_sources or [],
            "context": context or {},
        }

        if self._config.debug:
            self._logger.debug(
                "Gateway pre-check request",
                query=query[:50] if query else "",
                data_sources=data_sources,
            )

        response = await self._request(
            "POST",
            "/api/policy/pre-check",
            json_data=request_body,
        )

        if self._config.debug:
            self._logger.debug(
                "Gateway pre-check complete",
                context_id=response.get("context_id"),
                approved=response.get("approved"),
            )

        rate_limit = None
        if response.get("rate_limit"):
            rate_limit = RateLimitInfo(
                limit=response["rate_limit"]["limit"],
                remaining=response["rate_limit"]["remaining"],
                reset_at=_parse_datetime(response["rate_limit"]["reset_at"]),
            )

        return PolicyApprovalResult(
            context_id=response["context_id"],
            approved=response["approved"],
            requires_redaction=response.get("requires_redaction", False),
            approved_data=response.get("approved_data", {}),
            policies=response.get("policies", []),
            rate_limit_info=rate_limit,
            expires_at=_parse_datetime(response["expires_at"]),
            block_reason=response.get("block_reason"),
        )

    async def pre_check(
        self,
        user_token: str,
        query: str,
        data_sources: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyApprovalResult:
        """Alias for get_policy_approved_context().

        Perform policy pre-check before making LLM call.
        This is the first step in Gateway Mode.

        Args:
            user_token: JWT token for the user making the request
            query: The query/prompt that will be sent to the LLM
            data_sources: Optional list of MCP connectors to fetch data from
            context: Optional additional context for policy evaluation

        Returns:
            PolicyApprovalResult with context ID and approved data

        Example:
            >>> result = await client.pre_check(
            ...     user_token="user-jwt",
            ...     query="Find patients with diabetes",
            ...     data_sources=["postgres"]
            ... )
            >>> if not result.approved:
            ...     raise PolicyViolationError(result.block_reason)
        """
        return await self.get_policy_approved_context(
            user_token=user_token,
            query=query,
            data_sources=data_sources,
            context=context,
        )

    async def audit_llm_call(
        self,
        context_id: str,
        response_summary: str,
        provider: str,
        model: str,
        token_usage: TokenUsage,
        latency_ms: int,
        metadata: dict[str, Any] | None = None,
    ) -> AuditResult:
        """Report LLM call details for audit logging.

        This is the second step in Gateway Mode. Call this after making your
        LLM call to record it in the audit trail.

        Note:
            This is an enterprise feature that requires credentials.
            Set client_id and client_secret when creating the client.

        Args:
            context_id: Context ID from get_policy_approved_context()
            response_summary: Brief summary of the LLM response (not full response)
            provider: LLM provider name (openai, anthropic, bedrock, ollama)
            model: Model name (gpt-4, claude-3-sonnet, etc.)
            token_usage: Token counts from the LLM response
            latency_ms: Time taken for the LLM call in milliseconds
            metadata: Optional additional metadata to log

        Returns:
            AuditResult confirming the audit was recorded

        Raises:
            AuthenticationError: If credentials are not configured
            AxonFlowError: If audit recording fails

        Example:
            >>> result = await client.audit_llm_call(
            ...     context_id=ctx.context_id,
            ...     response_summary="Found 5 patients with recent lab results",
            ...     provider="openai",
            ...     model="gpt-4",
            ...     token_usage=TokenUsage(
            ...         prompt_tokens=100,
            ...         completion_tokens=50,
            ...         total_tokens=150
            ...     ),
            ...     latency_ms=250
            ... )
        """
        # Use smart default for client_id - enables zero-config community mode
        client_id = self._get_effective_client_id()

        request_body = {
            "context_id": context_id,
            "client_id": client_id,
            "response_summary": response_summary,
            "provider": provider,
            "model": model,
            "token_usage": {
                "prompt_tokens": token_usage.prompt_tokens,
                "completion_tokens": token_usage.completion_tokens,
                "total_tokens": token_usage.total_tokens,
            },
            "latency_ms": latency_ms,
            "metadata": metadata or {},
        }

        if self._config.debug:
            self._logger.debug(
                "Gateway audit request",
                context_id=context_id,
                provider=provider,
                model=model,
                tokens=token_usage.total_tokens,
            )

        response = await self._request(
            "POST",
            "/api/audit/llm-call",
            json_data=request_body,
        )

        if self._config.debug:
            self._logger.debug(
                "Gateway audit complete",
                audit_id=response.get("audit_id"),
            )

        return AuditResult(
            success=response["success"],
            audit_id=response["audit_id"],
        )

    async def audit_tool_call(
        self,
        request: AuditToolCallRequest,
    ) -> AuditToolCallResponse:
        """Record a non-LLM tool call in the audit trail.

        Use this to audit tool invocations (MCP tools, API calls, function
        calls) that are not LLM calls but should still appear in the audit
        trail for governance and compliance.

        Args:
            request: Tool call details including tool name, caller, input/output,
                and associated workflow/step information.

        Returns:
            AuditToolCallResponse confirming the audit entry was recorded.

        Raises:
            ValueError: If tool_name is empty.
            AxonFlowError: If audit recording fails.

        Note:
            `tool_type` is deprecated in favor of `caller_name` and is kept
            only for backward compatibility. New callers should set
            `caller_name` to identify which client made the call.

        Example:
            >>> from axonflow.types import AuditToolCallRequest
            >>> result = await client.audit_tool_call(
            ...     AuditToolCallRequest(
            ...         tool_name="getUserInfo",
            ...         caller_name="claude_code",
            ...         workflow_id="wf_abc123",
            ...         success=True,
            ...         duration_ms=45,
            ...     )
            ... )
            >>> print(result.audit_id)
        """
        if not request.tool_name or not request.tool_name.strip():
            msg = "tool_name is required and cannot be empty"
            raise ValueError(msg)

        request_body = request.model_dump(by_alias=True, exclude_none=True)

        if self._config.debug:
            self._logger.debug(
                "Audit tool call request",
                tool_name=request.tool_name,
                caller_name=request.caller_name,
                tool_type=request.tool_type,
            )

        response = await self._request(
            "POST",
            "/api/v1/audit/tool-call",
            json_data=request_body,
        )

        if self._config.debug:
            self._logger.debug(
                "Audit tool call complete",
                audit_id=response.get("audit_id"),
            )

        return AuditToolCallResponse(
            audit_id=response["audit_id"],
            status=response["status"],
            timestamp=response["timestamp"],
        )

    # =========================================================================
    # LLM Provider listing
    # =========================================================================

    async def list_providers(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[LLMProvider]:
        """List configured LLM providers.

        Calls ``GET /api/v1/llm-providers``. Optional filters narrow by
        provider type or enabled status; ``page`` / ``page_size`` request
        a specific page (server-side default: page 1, 20 items, max 100).

        Returns the providers from a SINGLE page. Use
        :meth:`list_providers_paged` if you need the pagination metadata,
        or :meth:`list_all_providers` to walk every page.

        Returns:
            List of :class:`LLMProvider` records.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> providers = await client.list_providers()
            >>> for p in providers:
            ...     print(p.name, p.type, p.health.status if p.health else "?")
        """
        result = await self.list_providers_paged(
            provider_type=provider_type, enabled=enabled, page=page, page_size=page_size
        )
        return result.providers

    async def list_providers_paged(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> LLMProviderListResponse:
        """List a page of LLM providers along with pagination metadata.

        Same filters as :meth:`list_providers` but returns the full
        :class:`LLMProviderListResponse` so callers can paginate.
        """
        query: dict[str, str] = {}
        if provider_type is not None:
            query["type"] = provider_type
        if enabled is not None:
            query["enabled"] = "true" if enabled else "false"
        if page is not None:
            query["page"] = str(int(page))
        if page_size is not None:
            query["page_size"] = str(int(page_size))

        path = "/api/v1/llm-providers"
        if query:
            path = f"{path}?{urlencode(query)}"
        response = await self._request("GET", path)

        raw_providers = response.get("providers") or []
        out: list[LLMProvider] = []
        for raw in raw_providers:
            try:
                health_raw = raw.get("health")
                health = LLMProviderHealth(**health_raw) if isinstance(health_raw, dict) else None
            except ValidationError as exc:
                # Don't let a single malformed health snapshot crash the
                # whole listing — surface it as a debug warning and continue
                # with health=None for that provider.
                self._logger.warning(
                    "LLMProvider %s has unparseable health snapshot, skipping: %s",
                    raw.get("name", "<unnamed>"),
                    exc,
                )
                health = None
            out.append(
                LLMProvider(
                    name=raw.get("name", ""),
                    type=raw.get("type", ""),
                    enabled=bool(raw.get("enabled", True)),
                    priority=int(raw.get("priority", 0) or 0),
                    weight=int(raw.get("weight", 0) or 0),
                    has_api_key=bool(raw.get("has_api_key", False)),
                    health=health,
                    endpoint=raw.get("endpoint"),
                    model=raw.get("model"),
                    region=raw.get("region"),
                    rate_limit=raw.get("rate_limit"),
                    timeout_seconds=raw.get("timeout_seconds"),
                    settings=raw.get("settings"),
                )
            )

        pagination_raw = response.get("pagination") or {}
        pagination = (
            PaginationMeta(**pagination_raw)
            if isinstance(pagination_raw, dict)
            else PaginationMeta()
        )
        return LLMProviderListResponse(providers=out, pagination=pagination)

    async def list_all_providers(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page_size: int = 100,
    ) -> list[LLMProvider]:
        """Walk every page of providers and return the full list.

        For deployments with many providers; uses ``page_size=100`` (the
        server-side max) by default to minimise round trips.
        """
        all_providers: list[LLMProvider] = []
        page = 1
        while True:
            result = await self.list_providers_paged(
                provider_type=provider_type, enabled=enabled, page=page, page_size=page_size
            )
            all_providers.extend(result.providers)
            if result.pagination.total_pages <= page or len(result.providers) == 0:
                break
            page += 1
        return all_providers

    # =========================================================================
    # Circuit Breaker Observability Methods
    # =========================================================================

    async def get_circuit_breaker_status(self) -> CircuitBreakerStatusResponse:
        """Get all active circuit breaker circuits.

        Returns the current state of all circuit breakers, including which
        circuits are open (tripped) and whether any emergency stop is active.

        Returns:
            CircuitBreakerStatusResponse with active circuits and counts.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> status = await client.get_circuit_breaker_status()
            >>> print(f"{status.count} active circuits")
            >>> if status.emergency_stop_active:
            ...     print("Emergency stop is active!")
        """
        if self._config.debug:
            self._logger.debug("Getting circuit breaker status")

        response = await self._request("GET", "/api/v1/circuit-breaker/status")
        data = response.get("data", response)

        return CircuitBreakerStatusResponse(
            active_circuits=data.get("active_circuits") or [],
            count=data.get("count", 0),
            emergency_stop_active=data.get("emergency_stop_active", False),
        )

    async def get_circuit_breaker_history(
        self,
        limit: int | None = None,
    ) -> CircuitBreakerHistoryResponse:
        """Get circuit breaker history for audit trail.

        Returns the history of circuit breaker state transitions, including
        trips, resets, and auto-recovery events.

        Args:
            limit: Maximum number of history entries to return.

        Returns:
            CircuitBreakerHistoryResponse with history entries.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> history = await client.get_circuit_breaker_history(limit=50)
            >>> for entry in history.history:
            ...     print(f"{entry.scope}/{entry.scope_id}: {entry.state}")
        """
        if self._config.debug:
            self._logger.debug(
                "Getting circuit breaker history",
                limit=limit,
            )

        path = "/api/v1/circuit-breaker/history"
        if limit is not None:
            path = f"{path}?limit={limit}"

        response = await self._request("GET", path)
        data = response.get("data", response)

        history = [CircuitBreakerHistoryEntry(**entry) for entry in (data.get("history") or [])]

        return CircuitBreakerHistoryResponse(
            history=history,
            count=data.get("count", 0),
        )

    async def get_circuit_breaker_config(
        self,
        tenant_id: str | None = None,
    ) -> CircuitBreakerConfig:
        """Get circuit breaker configuration (global or tenant-specific).

        Args:
            tenant_id: If provided, returns tenant-specific config with
                any overrides applied. Otherwise returns global defaults.

        Returns:
            CircuitBreakerConfig with thresholds and recovery settings.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> config = await client.get_circuit_breaker_config()
            >>> print(f"Error threshold: {config.error_threshold}")
            >>> tenant_config = await client.get_circuit_breaker_config(
            ...     tenant_id="tenant-123"
            ... )
        """
        if self._config.debug:
            self._logger.debug(
                "Getting circuit breaker config",
                tenant_id=tenant_id,
            )

        path = "/api/v1/circuit-breaker/config"
        if tenant_id is not None:
            path = f"{path}?tenant_id={tenant_id}"

        response = await self._request("GET", path)
        data = response.get("data", response)

        return CircuitBreakerConfig(**data)

    async def update_circuit_breaker_config(
        self,
        config: CircuitBreakerConfigUpdate,
    ) -> dict[str, Any]:
        """Update per-tenant circuit breaker configuration.

        Sets tenant-specific overrides for circuit breaker thresholds and
        recovery behavior.

        Args:
            config: Configuration update with tenant_id and override values.

        Returns:
            Server response confirming the update.

        Raises:
            ValueError: If tenant_id is empty.
            AxonFlowError: If the request fails.

        Example:
            >>> from axonflow.types import CircuitBreakerConfigUpdate
            >>> result = await client.update_circuit_breaker_config(
            ...     CircuitBreakerConfigUpdate(
            ...         tenant_id="tenant-123",
            ...         error_threshold=10,
            ...         violation_threshold=5,
            ...     )
            ... )
        """
        if not config.tenant_id or not config.tenant_id.strip():
            msg = "tenant_id is required and cannot be empty"
            raise ValueError(msg)

        if self._config.debug:
            self._logger.debug(
                "Updating circuit breaker config",
                tenant_id=config.tenant_id,
            )

        request_body = config.model_dump(by_alias=True, exclude_none=True)

        response = await self._request(
            "PUT",
            "/api/v1/circuit-breaker/config",
            json_data=request_body,
        )

        result: dict[str, Any] = response.get("data", response)
        return result

    # =========================================================================
    # Policy Simulation Methods (Evaluation Tier+)
    # =========================================================================

    async def simulate_policies(
        self,
        query: str,
        request_type: str | None = None,
        user: dict[str, Any] | None = None,
        client: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> SimulatePoliciesResponse:
        """Simulate all active policies against input (dry run).

        Runs the full policy evaluation pipeline without actually blocking
        or auditing the request. Useful for testing policy configurations
        before deploying them.

        Requires Evaluation tier or above.

        Args:
            query: The query text to simulate.
            request_type: Optional request type (e.g. 'chat', 'completion').
            user: Optional user context dictionary.
            client: Optional client context dictionary.
            context: Optional additional context dictionary.

        Returns:
            SimulatePoliciesResponse with simulation results.

        Raises:
            AxonFlowError: If the request fails (e.g. 403 for Community tier).

        Example:
            >>> result = await client.simulate_policies(
            ...     query="What is the patient's SSN?",
            ...     request_type="chat",
            ...     user={"role": "analyst"},
            ... )
            >>> print(f"Allowed: {result.allowed}, Risk: {result.risk_score}")
            >>> print(f"Policies matched: {result.applied_policies}")
        """
        if self._config.debug:
            self._logger.debug("Simulating policies", query=query[:50])

        body: dict[str, Any] = {"query": query}
        if request_type is not None:
            body["request_type"] = request_type
        if user is not None:
            body["user"] = user
        if client is not None:
            body["client"] = client
        if context is not None:
            body["context"] = context

        response = await self._orchestrator_request(
            "POST", "/api/v1/policies/simulate", json_data=body
        )
        data = response.get("data", response) if isinstance(response, dict) else {}

        return SimulatePoliciesResponse.model_validate(data)

    async def get_policy_impact_report(
        self,
        policy_id: str,
        inputs: list[dict[str, Any]],
    ) -> ImpactReportResponse:
        """Test a single policy against multiple inputs.

        Generates an impact report showing how a specific policy would
        affect a set of sample inputs. Useful for understanding the
        blast radius of policy changes.

        Requires Evaluation tier or above.

        Args:
            policy_id: ID of the policy to test.
            inputs: List of input dictionaries, each containing at minimum
                a 'query' key and optionally 'request_type', 'user', 'context'.

        Returns:
            ImpactReportResponse with per-input match/block results.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> report = await client.get_policy_impact_report(
            ...     policy_id="pol_abc123",
            ...     inputs=[
            ...         {"query": "Show me SSN 123-45-6789"},
            ...         {"query": "What is the weather today?"},
            ...     ],
            ... )
            >>> print(f"Match rate: {report.match_rate:.0%}")
            >>> print(f"Block rate: {report.block_rate:.0%}")
        """
        if self._config.debug:
            self._logger.debug(
                "Getting policy impact report",
                policy_id=policy_id,
                input_count=len(inputs),
            )

        body: dict[str, Any] = {
            "policy_id": policy_id,
            "inputs": inputs,
        }

        response = await self._orchestrator_request(
            "POST", "/api/v1/policies/impact-report", json_data=body
        )
        data = response.get("data", response) if isinstance(response, dict) else {}

        return ImpactReportResponse.model_validate(data)

    async def detect_policy_conflicts(
        self,
        policy_id: str | None = None,
    ) -> PolicyConflictResponse:
        """Detect conflicts between active policies.

        Analyzes active policies for conflicts such as overlapping
        conditions with contradictory actions. Optionally scoped to
        conflicts involving a specific policy.

        Requires Evaluation tier or above.

        Args:
            policy_id: If provided, only check conflicts involving this policy.
                If None, check all active policies.

        Returns:
            PolicyConflictResponse with detected conflicts.

        Raises:
            AxonFlowError: If the request fails.

        Example:
            >>> conflicts = await client.detect_policy_conflicts()
            >>> print(f"Found {conflicts.conflict_count} conflicts")
            >>> for c in conflicts.conflicts:
            ...     print(f"{c.policy_a.name} vs {c.policy_b.name}: {c.description}")
        """
        if self._config.debug:
            self._logger.debug("Detecting policy conflicts", policy_id=policy_id)

        body: dict[str, Any] = {}
        if policy_id is not None:
            body["policy_id"] = policy_id

        response = await self._orchestrator_request(
            "POST", "/api/v1/policies/conflicts", json_data=body
        )
        data = response.get("data", response) if isinstance(response, dict) else {}

        return PolicyConflictResponse.model_validate(data)

    # =========================================================================
    # Audit Log Read Methods
    # =========================================================================

    async def search_audit_logs(
        self,
        request: AuditSearchRequest | None = None,
    ) -> AuditSearchResponse:
        """Search audit logs with optional filters.

        Query the AxonFlow orchestrator for audit logs matching the specified
        criteria. Use this for compliance dashboards, security investigations,
        and operational monitoring.

        Args:
            request: Search filters and pagination options. If None, returns
                recent logs with default limit (100).

        Returns:
            AuditSearchResponse containing matching audit entries.

        Example:
            >>> from datetime import datetime, timedelta
            >>> from axonflow.types import AuditSearchRequest
            >>>
            >>> # Search for logs from a specific user in the last 24 hours
            >>> yesterday = datetime.now() - timedelta(days=1)
            >>> request = AuditSearchRequest(
            ...     user_email="analyst@company.com",
            ...     start_time=yesterday,
            ...     limit=100,
            ... )
            >>> result = await client.search_audit_logs(request)
            >>> for entry in result.entries:
            ...     print(f"[{entry.timestamp}] {entry.user_email}: {entry.query_summary}")
        """
        if request is None:
            request = AuditSearchRequest()

        body = _build_audit_search_body(request)

        if self._config.debug:
            self._logger.debug(
                "Searching audit logs",
                limit=request.limit,
                offset=request.offset,
            )

        response = await self._orchestrator_request(
            "POST",
            "/api/v1/audit/search",
            scoped_resource="audit entries",
            scoped_page_key="entries",
            json_data=body,
        )

        # API may return array directly or wrapped response
        if isinstance(response, list):
            entries = [AuditLogEntry.model_validate(e) for e in response]
            return AuditSearchResponse(
                entries=entries,
                total=len(entries),
                limit=request.limit,
                offset=request.offset,
            )
        # Wrapped response format (response is dict at this point)
        if not isinstance(response, dict):
            response = {}
        raw_entries = response.get("entries")
        if not isinstance(raw_entries, list):
            raw_entries = []
        entries = [AuditLogEntry.model_validate(e) for e in raw_entries]
        return AuditSearchResponse(
            entries=entries,
            total=response.get("total", len(entries)),
            limit=response.get("limit", request.limit),
            offset=response.get("offset", request.offset),
        )

    def as_user(self, user_token: str | None) -> AxonFlow:
        """A client identical to this one but presenting ``user_token``.

        The shape to reach for when one process acts on behalf of several
        people — a gateway, a bot. Unlike the per-call ``user_token=`` keyword,
        which only the read methods accept, this reaches EVERY method: there is
        no carve-out to remember and no path on which the identity silently
        widens back to the process's own.

        ::

            for_alice = client.as_user(alice_token)
            rows = await for_alice.list_decisions()

        The returned client shares this one's CONNECTION POOL and cache, so
        deriving one per request is cheap. It gets its own thin httpx clients,
        because the identity travels on a request event hook that closes over a
        client's own config — sharing the httpx objects outright would give the
        derived client this one's identity, silently. This client is not
        modified.

        An empty token returns a client presenting no identity at all, which on
        an enterprise stack reads nothing (see :data:`ReadScope.NONE`).
        """
        derived = object.__new__(type(self))
        for slot in type(self).__slots__:
            object.__setattr__(derived, slot, getattr(self, slot))
        derived_config = self._config.model_copy(
            update={"user_token": (user_token or "").strip() or None}
        )
        object.__setattr__(derived, "_config", derived_config)

        # Every LAZILY-BOUND sub-object is reset, not copied. Each caches a
        # reference to the client that created it, so copying one would hand the
        # derived client a namespace that still calls through the PARENT — with
        # the parent's identity. The bug only appears if the parent touched the
        # namespace before deriving, which is exactly the ordering a long-lived
        # gateway has.
        for slot in _LAZY_NAMESPACE_SLOTS:
            object.__setattr__(derived, slot, None)

        # New httpx clients that BORROW this one's transport and mounts: the
        # connection pool is shared (so deriving one per request stays cheap)
        # and the environment proxy map survives, but the identity hook is not
        # shared — it closes over a client's own config, so reusing the httpx
        # objects wholesale would silently give the derived client the ORIGINAL
        # identity. (It did; test_as_user_reaches_every_method caught it.)
        async def _stamp_derived(request: httpx.Request) -> None:
            stamp_read_identity(
                derived_config.user_token, request, endpoint=derived_config.endpoint
            )

        hooks: dict[str, list[Any]] = {"request": [_stamp_derived]}
        for slot, parent_client in (
            ("_http_client", self._http_client),
            ("_map_http_client", self._map_http_client),
        ):
            borrowed = httpx.AsyncClient(
                timeout=parent_client.timeout,
                transport=parent_client._transport,  # noqa: SLF001 - see above
                headers=parent_client.headers,
                event_hooks=hooks,
            )
            # httpx only populates _mounts when it builds the transport itself,
            # so the proxy map is carried across explicitly rather than lost.
            borrowed._mounts = dict(parent_client._mounts)  # noqa: SLF001
            object.__setattr__(derived, slot, borrowed)
        return derived

    async def explain_decision(
        self,
        decision_id: str,
        *,
        user_token: str | None = None,
    ) -> DecisionExplanation:
        """Fetch the full explanation for a previously-made policy decision.

        Implements ADR-043. Calls ``GET /api/v1/decisions/:id/explain`` and
        returns a :class:`DecisionExplanation` with matched policies, risk
        level, override availability, and a rolling-24h session hit count.

        **Which decisions this returns (platform #2922).** The read is scoped
        to the per-user identity the caller presents, NOT to the tenant
        credential. On an enterprise stack:

        * a tenant-wide role (admin, owner, policy_admin) explains any decision
          in the tenant;
        * any other identity (developer, viewer) explains only the decisions
          attributed to it — another user's decision answers exactly like a
          decision that does not exist;
        * a caller presenting NO identity explains nothing at all. Every call
          answers not-found, whatever the decision id.

        Community and Community-SaaS deployments are single-operator and read
        tenant-wide with no identity needed.

        **Telling the three misses apart.** A miss raises
        :class:`~axonflow.read_identity.ReadScopeError` whenever the platform's
        ``X-Axonflow-Read-Scope`` header says the caller's scope decided it, so
        "not yours" and "you presented nothing" are distinguishable from "past
        retention" instead of all three arriving as the same 404.

        Args:
            decision_id: The global decision identifier returned in the
                original step gate or policy evaluation response.
            user_token: Per-user identity for THIS call only, overriding the
                client-wide ``user_token``. Use it when one process acts on
                behalf of several people. An empty string is not an identity:
                it makes this read explicitly unidentified rather than falling
                back to the client-wide one.

        Returns:
            A DecisionExplanation (frozen shape per ADR-043).

        Raises:
            ValueError: If ``decision_id`` is empty.
            ReadScopeError: The decision is not visible to the identity
                presented, or no identity was resolved. Check
                ``err.identity_missing`` to tell those apart.

        Example:
            >>> exp = await client.explain_decision("dec_wf123_step4")
            >>> if exp.override_available:
            ...     # offer the user a governed override action
            ...     pass
        """
        if not decision_id:
            msg = "decision_id is required"
            raise ValueError(msg)

        # Path-escape the decision ID. The data contract doesn't constrain
        # the identifier format — IDs containing "/" or "?" would break the URL.
        encoded = quote(decision_id, safe="")

        with use_read_identity(user_token):
            response = await self._orchestrator_request(
                "GET",
                f"/api/v1/decisions/{encoded}/explain",
                scoped_resource="decision",
                scoped_identifier=decision_id,
            )

        if not isinstance(response, dict):
            response = {}
        return DecisionExplanation.model_validate(response)

    async def list_decisions(
        self,
        opts: ListDecisionsOptions | None = None,
        *,
        user_token: str | None = None,
    ) -> list[DecisionSummary]:
        """List the recent policy decisions VISIBLE TO THE CALLER.

        Implements ``GET /api/v1/decisions``. Returns the slim 5-field
        :class:`DecisionSummary` page; the platform applies a tier-gated
        cap (5/24h Free + Community, 100/30d Pro + Evaluation, 1000/full
        retention Enterprise). Over-cap requests yield a 429 with the V1
        upgrade envelope, surfaced as
        :class:`axonflow.exceptions.RateLimitError` carrying the parsed
        ``upgrade.{tier,compare_url,buy_url}`` so the caller can branch on
        them without re-parsing the body.

        **Whose decisions come back (platform #2922).** Not the tenant's — the
        caller's SCOPE. On an enterprise stack a tenant-wide role (admin,
        owner, policy_admin) lists the whole tenant, any other identity lists
        only its own rows, and a caller presenting NO identity lists nothing
        whatsoever.

        **The empty list that was never true.** That last case used to return
        an empty list, which reads as "your tenant has made no decisions" and
        is a different statement from what happened. When the platform reports
        ``X-Axonflow-Read-Scope: none`` and the result is empty, this method
        now raises :class:`~axonflow.read_identity.ReadScopeError` instead —
        the read could not have returned a row, so its emptiness is not
        evidence about the data. Callers upgrading from an earlier SDK on an
        enterprise stack will see this exactly where they were being told
        nothing was there; the remedy is to present an identity.

        A genuinely empty own-rows or tenant-wide read is NOT an error: those
        callers could have seen rows and there were none.

        Args:
            opts: Filter + page-size options. ``None`` returns the
                tier-default page visible to the caller.
            user_token: Per-user identity for THIS call only, overriding the
                client-wide ``user_token``.

        Returns:
            A list of DecisionSummary rows ordered newest-first.

        Raises:
            RateLimitError: 429 tier-cap; ``rle.upgrade`` exposes
                tier/compare_url/buy_url.
            ReadScopeError: The page was empty because no per-user identity
                was resolved, so it could not have contained a row.
            AxonFlowError: Other HTTP errors (401, 5xx, etc.).

        Example:
            >>> from axonflow.decisions import ListDecisionsOptions
            >>> opts = ListDecisionsOptions(decision="blocked", limit=10)
            >>> for d in await client.list_decisions(opts):
            ...     print(d.decision_id, d.decision, d.timestamp)
        """
        path = "/api/v1/decisions"
        qs = _build_list_decisions_query(opts)
        if qs:
            path = f"{path}?{qs}"

        # Hand-roll the request so we can branch on 429 BEFORE
        # raise_for_status promotes it to a generic AxonFlowError.
        # Other failure modes fall through to the same shape
        # _orchestrator_request would have produced.
        self._pre_request_hook()
        url = f"{self._config.endpoint}{path}"
        try:
            with use_read_identity(user_token):
                response = await self._http_client.get(url)
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Orchestrator: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e

        if response.status_code == 429:  # noqa: PLR2004
            # Try to parse the V1 envelope. Malformed body falls back to
            # a plain RateLimitError without upgrade context — never
            # silently succeed.
            try:
                envelope = response.json()
            except (ValueError, json.JSONDecodeError):
                envelope = {}
            limit_type = envelope.get("limit_type")
            upgrade_dict = envelope.get("upgrade")
            upgrade = None
            if isinstance(upgrade_dict, dict):
                upgrade = UpgradeInfo(
                    tier=upgrade_dict.get("tier", ""),
                    wording=upgrade_dict.get("wording", ""),
                    compare_url=upgrade_dict.get("compare_url", ""),
                    buy_url=upgrade_dict.get("buy_url", ""),
                )
            error_msg = envelope.get("error")
            if not isinstance(error_msg, str) or error_msg == "":
                error_msg = "rate limit exceeded"
            raw_limit = envelope.get("limit")
            raw_remaining = envelope.get("remaining")
            raise RateLimitError(
                error_msg,
                limit=int(raw_limit) if isinstance(raw_limit, (int, float)) else 0,
                remaining=(int(raw_remaining) if isinstance(raw_remaining, (int, float)) else 0),
                limit_type=limit_type,
                tier=envelope.get("tier"),
                upgrade=upgrade,
            )

        if response.status_code >= 400:  # noqa: PLR2004
            msg = f"HTTP {response.status_code}: {response.text}"
            raise AxonFlowError(msg)

        body = response.json()
        rows = body.get("decisions") if isinstance(body, dict) else None
        decisions = (
            [DecisionSummary.model_validate(r) for r in rows] if isinstance(rows, list) else []
        )

        # An empty page under ReadScope.NONE is the fail-closed shape, not a
        # finding: the platform returned zero rows because it had no identity
        # to scope on, so the page says nothing about what exists. Guarded on
        # emptiness as well as on the scope so a non-empty page is never turned
        # into an error, whatever the header says.
        #
        # Only NONE refuses. OWN_ROWS with zero rows is a real answer ("you
        # have made no decisions matching this filter"), and turning it into an
        # error would replace one wrong report with another.
        scoped = refuse_vacuous_scoped_page(response, "decisions", len(decisions))
        if scoped is not None:
            raise scoped
        return decisions

    # ------------------------------------------------------------------ #
    # AuthZEN-native authorization (ADR-065)                              #
    # ------------------------------------------------------------------ #

    async def _send_authzen(
        self,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[int, bytes]:
        """The transport :mod:`axonflow.authzen` runs its envelopes through.

        It is this client's own authenticated HTTP path — same credentials,
        same retry policy, same ``X-Axonflow-Client`` attribution — with the
        status left uninterpreted, because on this route a 4xx body is a typed
        refusal document rather than an error string.
        """
        response = await self._send_raw("POST", path, json_data=body, headers=headers)
        return response.status_code, response.content

    async def evaluate(self, request: AuthZENRequest) -> AuthZENDecision:
        """Ask whether one subject may perform one action on one resource.

        The AuthZEN-native surface (``POST /api/v1/access/evaluation``). New
        integrations should be written against this rather than
        :meth:`decide`: at v11 the engine behind it becomes the ADR-065 Policy
        Decision Point with no wire change, so an integration written here
        migrates once instead of twice.

        Example:
            >>> from axonflow import (
            ...     AuthZENAction, AuthZENRequest, AuthZENResource, AuthZENSubject,
            ... )
            >>> decision = await client.evaluate(
            ...     AuthZENRequest(
            ...         subject=AuthZENSubject(type="gateway", id="llm-gateway-01"),
            ...         action=AuthZENAction(name="llm.completion"),
            ...         resource=AuthZENResource(type="llm", id="llm"),
            ...         context={"args": {"query": user_prompt}},
            ...     )
            ... )
            >>> if not decision.allowed:
            ...     raise RuntimeError(f"blocked: {decision.state}")

        Raises:
            AuthZENRefusal: the request was not evaluated. This is NOT a
                denial — ``.pointer`` names the member to fix, and only
                ``.retryable`` is worth sending again.
            AuthZENProtocolError: the server answered 200 with a body this
                build cannot safely act on.
            AuthenticationError: 401 — the gateway refused the credentials
                before the route ran.
        """
        return await evaluate_envelope(self._send_authzen, build_envelope(evaluation=request))

    async def evaluate_all(self, bulk: AuthZENBulk) -> AuthZENDecision:
        """Ask whether ONE operation is permitted against several preconditions.

        It returns ONE decision, not one per entry. The entries of a bulk
        request are preconditions of a single operation — moving a ticket must
        be authorized against the destination project as well as against the
        ticket — so they combine to the least permissive outcome: one denied
        entry denies the operation. An API returning a list would invite a
        caller to act on the entry it liked.

        Any member an entry omits is inherited from the envelope's shared base,
        so the common case is a shared subject and action with one resource per
        entry.

        Example:
            >>> decision = await client.evaluate_all(
            ...     AuthZENBulk(
            ...         subject=AuthZENSubject(type="gateway", id="llm-gateway-01"),
            ...         action=AuthZENAction(name="tool.call"),
            ...         context={"args": {"query": user_prompt}},
            ...         evaluations=[
            ...             AuthZENRequest(
            ...                 resource=AuthZENResource(type="tool", id="jira/move_issue"),
            ...             ),
            ...             AuthZENRequest(
            ...                 resource=AuthZENResource(type="tool", id="jira/update_project"),
            ...             ),
            ...         ],
            ...     )
            ... )
        """
        return await evaluate_envelope(self._send_authzen, build_envelope(evaluations=bulk))

    # ------------------------------------------------------------------ #
    # Decision Mode PEP: decide -> fulfill -> forward (ADR-056, #2563)    #
    # ------------------------------------------------------------------ #

    async def decide(self, request: DecideRequest) -> DecideResponse:
        """Ask the PDP for a verdict on a request (``POST /api/v1/decide``).

        This is the PDP step of a PEP. ``/decide`` is a pure decision point: it
        NEVER mutates content. When an allow verdict carries a ``redact_pii``
        obligation, discharge it with :meth:`fulfill_request` (or use the
        one-call :meth:`decide_and_fulfill`) — never by redacting locally.

        Decision Mode auth is HTTP Basic (org:license), which this client
        already sends; demo / wrong credentials are refused with 401 →
        :class:`~axonflow.exceptions.AuthenticationError`. A deny verdict is
        returned in the body with HTTP 200, not as an error.

        Args:
            request: The :class:`DecideRequest` (``stage`` ∈ {"llm","tool",
                "agent"} and ``query`` are required).

        Returns:
            The :class:`DecideResponse` verdict, with ``obligations`` always a
            (possibly empty) list.

        Raises:
            AuthenticationError: 401 (bad / demo credentials).
            AxonFlowError: Other non-200 responses.
        """
        self._pre_request_hook()
        url = f"{self._config.endpoint}{PEP_DECIDE_PATH}"
        body = request.model_dump(exclude_none=True)
        try:
            response = await self._http_client.post(url, json=body)
        except httpx.ConnectError as e:
            msg = f"Failed to connect to AxonFlow Agent: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e

        if response.status_code == 401:  # noqa: PLR2004
            msg = "Invalid credentials"
            raise AuthenticationError(msg)
        if response.status_code >= 400:  # noqa: PLR2004
            msg = f"HTTP {response.status_code}: {response.text}"
            raise AxonFlowError(msg)

        data = response.json()
        if not isinstance(data, dict):
            data = {}
        return DecideResponse.model_validate(data)

    async def fulfill_request(
        self,
        decision: DecideResponse,
        statement: str,
    ) -> tuple[str, bool]:
        """Discharge every request-phase ``redact_pii`` obligation on ``decision``.

        For each request-phase ``redact_pii`` obligation, POSTs ``statement`` to
        the engine endpoint the obligation names (``check-input``) and returns
        the engine-redacted statement to forward.

        There is NO code path in which this method redacts locally — fulfillment
        is always the engine round-trip (ADR-056 / #2563).

        Returns:
            ``(content, did_redact)``. ``content`` is the engine-redacted
            statement (or the original when no obligation mutates the request).
            ``did_redact`` reflects whether the ENGINE actually changed the
            content, not merely that an obligation was present.

        Raises:
            ObligationNotFulfillableError: A ``redact_pii`` obligation could not
                be discharged through the engine — it named no request-phase
                fulfillment, advertised a content-type the PEP is not holding,
                named an endpoint this client will not call, the engine call
                failed, or the engine reported the redactor did not run
                (``redaction_evaluated=false``). The caller MUST fail closed
                (block) — never forward the original ``statement``.
        """
        redacted = statement
        did_redact = False
        for ob in decision.obligations:
            if ob.type != OBLIGATION_REDACT_PII:
                # redact_pii is the only content-mutating obligation today;
                # other types are pass-through by contract.
                continue
            if ob.fulfillment is None or ob.fulfillment.phase != PHASE_REQUEST:
                msg = "redact_pii obligation missing request-phase fulfillment"
                raise ObligationNotFulfillableError(msg)
            content_types = ob.fulfillment.content_types
            if content_types and CONTENT_TYPE_TEXT not in content_types:
                msg = f"fulfillment endpoint does not advertise a {CONTENT_TYPE_TEXT} detector"
                raise ObligationNotFulfillableError(msg)
            if not pep_endpoint_path_matches(ob.fulfillment.endpoint, PEP_REQUEST_REDACTION_PATH):
                msg = (
                    f"fulfillment endpoint {ob.fulfillment.endpoint!r} is not "
                    "the request-redaction endpoint"
                )
                raise ObligationNotFulfillableError(msg)
            redacted = await self._fulfill_via_check_input(redacted)
            if redacted != statement:
                did_redact = True
        return redacted, did_redact

    async def _fulfill_via_check_input(self, statement: str) -> str:
        """POST ``statement`` to the request-redaction engine endpoint.

        Returns the engine-masked statement. Fails closed (raises
        :class:`ObligationNotFulfillableError`) when the engine call errors, the
        engine returns non-200, or ``redaction_evaluated`` is false — never
        returns unredacted content under an unfulfillable condition.
        """
        try:
            result = await self.mcp_check_input(
                connector_type="gateway",
                statement=statement,
                operation="execute",
                content_type=CONTENT_TYPE_TEXT,
            )
        except AxonFlowError as e:
            msg = f"request-redaction engine call failed: {e}"
            raise ObligationNotFulfillableError(msg) from e
        # FAIL CLOSED if the redactor did not actually run (#2563 B1). Without
        # this the PEP cannot distinguish "engine looked, found nothing" (safe
        # to forward) from "engine wasn't looking" (would leak PII).
        if not result.redaction_evaluated:
            msg = "engine reported the redactor did not run (redaction disabled)"
            raise ObligationNotFulfillableError(msg)
        if result.redacted:
            # FAIL CLOSED on a self-contradictory engine response: redacted=true
            # with no redacted_statement means the engine claims it masked
            # something but gave us nothing to forward — never fall back to the
            # unredacted original.
            if not result.redacted_statement:
                msg = "engine reported redacted=true but returned no redacted_statement"
                raise ObligationNotFulfillableError(msg)
            return result.redacted_statement
        # Redactor ran and found nothing to mask — forward unchanged.
        return statement

    async def decide_and_fulfill(
        self,
        request: DecideRequest,
    ) -> tuple[str, str, DecideResponse]:
        """One-call PEP path: decide, then fulfill any request-phase obligation.

        Returns ``(verdict, content, decision)``. Branch on ``verdict``: forward
        ``content`` on ``"allow"``; block on ``"deny"`` / ``"needs_approval"``.

        On the not-fulfillable path this raises
        :class:`ObligationNotFulfillableError` AFTER having computed an empty
        ``content`` internally, so a caller that catches the error cannot
        accidentally forward the unredacted query — fail-closed by construction.
        """
        decision = await self.decide(request)
        if decision.verdict != VERDICT_ALLOW:
            return decision.verdict, request.query, decision
        redacted, _ = await self.fulfill_request(decision, request.query)
        return decision.verdict, redacted, decision

    async def get_audit_logs_by_tenant(
        self,
        tenant_id: str,
        options: AuditQueryOptions | None = None,
    ) -> AuditSearchResponse:
        """Get recent audit logs for a specific tenant.

        Convenience method for tenant-scoped audit queries. Use this when you
        need to view all recent activity for a specific tenant.

        Args:
            tenant_id: The tenant identifier to query
            options: Pagination options (limit, offset)

        Returns:
            AuditSearchResponse containing audit entries for the tenant.

        Raises:
            ValueError: If tenant_id is empty

        Example:
            >>> # Get the last 50 audit logs for a tenant
            >>> result = await client.get_audit_logs_by_tenant("tenant-abc")
            >>> print(f"Found {len(result.entries)} entries")
            >>>
            >>> # With custom options
            >>> from axonflow.types import AuditQueryOptions
            >>> opts = AuditQueryOptions(limit=100, offset=50)
            >>> result = await client.get_audit_logs_by_tenant("tenant-abc", opts)
        """
        if not tenant_id:
            msg = "tenant_id is required"
            raise ValueError(msg)

        if options is None:
            options = AuditQueryOptions()

        if self._config.debug:
            self._logger.debug(
                "Getting audit logs for tenant",
                tenant_id=tenant_id,
                limit=options.limit,
                offset=options.offset,
            )

        url = f"/api/v1/audit/tenant/{tenant_id}?limit={options.limit}&offset={options.offset}"
        response = await self._orchestrator_request(
            "GET",
            url,
            scoped_resource="audit entries",
            scoped_page_key="entries",
        )

        # API may return array directly or wrapped response
        if isinstance(response, list):
            entries = [AuditLogEntry.model_validate(e) for e in response]
            return AuditSearchResponse(
                entries=entries,
                total=len(entries),
                limit=options.limit,
                offset=options.offset,
            )
        # Wrapped response format (response is dict at this point)
        if not isinstance(response, dict):
            response = {}
        raw_entries = response.get("entries")
        if not isinstance(raw_entries, list):
            raw_entries = []
        entries = [AuditLogEntry.model_validate(e) for e in raw_entries]
        return AuditSearchResponse(
            entries=entries,
            total=response.get("total", len(entries)),
            limit=response.get("limit", options.limit),
            offset=response.get("offset", options.offset),
        )

    # =========================================================================
    # Policy CRUD Methods - Static Policies
    # =========================================================================

    async def list_static_policies(
        self,
        options: ListStaticPoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """List all static policies with optional filtering.

        Args:
            options: Filtering and pagination options

        Returns:
            List of static policies

        Example:
            >>> policies = await client.list_static_policies(
            ...     ListStaticPoliciesOptions(category=PolicyCategory.SECURITY_SQLI)
            ... )
        """
        params: list[str] = []
        if options:
            if options.category:
                params.append(f"category={options.category.value}")
            if options.tier:
                params.append(f"tier={options.tier.value}")
            if options.organization_id:
                params.append(f"organization_id={options.organization_id}")
            if options.enabled is not None:
                params.append(f"enabled={str(options.enabled).lower()}")
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")
            if options.sort_by:
                params.append(f"sort_by={options.sort_by}")
            if options.sort_order:
                params.append(f"sort_order={options.sort_order}")
            if options.search:
                params.append(f"search={options.search}")

        path = "/api/v1/static-policies"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing static policies", path=path)

        response = await self._request("GET", path)
        # Backend returns { policies: [], pagination: {} }, extract the policies array
        policies = response.get("policies", []) if isinstance(response, dict) else response
        return [StaticPolicy.model_validate(p) for p in policies]

    async def get_static_policy(self, policy_id: str) -> StaticPolicy:
        """Get a specific static policy by ID.

        Args:
            policy_id: Policy ID

        Returns:
            The static policy
        """
        if self._config.debug:
            self._logger.debug("Getting static policy", policy_id=policy_id)

        response = await self._request("GET", f"/api/v1/static-policies/{policy_id}")
        return StaticPolicy.model_validate(response)

    async def create_static_policy(
        self,
        request: CreateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Create a new static policy.

        Args:
            request: Policy creation request

        Returns:
            The created policy

        Example:
            >>> policy = await client.create_static_policy(
            ...     CreateStaticPolicyRequest(
            ...         name="Block Credit Cards",
            ...         category=PolicyCategory.PII_GLOBAL,
            ...         pattern=r"\\b(?:\\d{4}[- ]?){3}\\d{4}\\b",
            ...         severity=8
            ...     )
            ... )
        """
        if self._config.debug:
            self._logger.debug("Creating static policy", name=request.name)

        response = await self._request(
            "POST",
            "/api/v1/static-policies",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return StaticPolicy.model_validate(response)

    async def update_static_policy(
        self,
        policy_id: str,
        request: UpdateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Update an existing static policy.

        Args:
            policy_id: Policy ID
            request: Fields to update

        Returns:
            The updated policy
        """
        if self._config.debug:
            self._logger.debug("Updating static policy", policy_id=policy_id)

        response = await self._request(
            "PUT",
            f"/api/v1/static-policies/{policy_id}",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return StaticPolicy.model_validate(response)

    async def delete_static_policy(self, policy_id: str) -> None:
        """Delete a static policy.

        Args:
            policy_id: Policy ID
        """
        if self._config.debug:
            self._logger.debug("Deleting static policy", policy_id=policy_id)

        await self._request("DELETE", f"/api/v1/static-policies/{policy_id}")

    async def toggle_static_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> StaticPolicy:
        """Toggle a static policy's enabled status.

        Args:
            policy_id: Policy ID
            enabled: Whether the policy should be enabled

        Returns:
            The updated policy
        """
        if self._config.debug:
            self._logger.debug("Toggling static policy", policy_id=policy_id, enabled=enabled)

        response = await self._request(
            "PATCH",
            f"/api/v1/static-policies/{policy_id}",
            json_data={"enabled": enabled},
        )
        return StaticPolicy.model_validate(response)

    async def get_effective_static_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """Get effective static policies with tier inheritance applied.

        Args:
            options: Filtering options

        Returns:
            List of effective policies
        """
        query_params: list[str] = []
        if options:
            if options.category:
                query_params.append(f"category={options.category.value}")
            if options.include_disabled:
                query_params.append("include_disabled=true")
            if options.include_overridden:
                query_params.append("include_overridden=true")

        path = "/api/v1/static-policies/effective"
        if query_params:
            path = f"{path}?{'&'.join(query_params)}"

        if self._config.debug:
            self._logger.debug("Getting effective static policies", path=path)

        response = await self._request("GET", path)
        # Backend returns { static: [], dynamic: [], ... }, extract the static array
        policies = response.get("static", []) if isinstance(response, dict) else response
        return [StaticPolicy.model_validate(p) for p in policies]

    async def test_pattern(
        self,
        pattern: str,
        test_inputs: list[str],
    ) -> TestPatternResult:
        """Test a regex pattern against sample inputs.

        Args:
            pattern: Regex pattern to test
            test_inputs: Array of strings to test against

        Returns:
            Test results showing matches

        Example:
            >>> result = await client.test_pattern(
            ...     r"\\b\\d{3}-\\d{2}-\\d{4}\\b",
            ...     ["SSN: 123-45-6789", "No SSN here"]
            ... )
        """
        if self._config.debug:
            self._logger.debug(
                "Testing pattern",
                pattern=pattern,
                input_count=len(test_inputs),
            )

        response = await self._request(
            "POST",
            "/api/v1/static-policies/test",
            json_data={"pattern": pattern, "inputs": test_inputs},
        )
        return TestPatternResult.model_validate(response)

    async def get_static_policy_versions(
        self,
        policy_id: str,
    ) -> list[PolicyVersion]:
        """Get version history for a static policy.

        Args:
            policy_id: Policy ID

        Returns:
            Array of version history entries
        """
        if self._config.debug:
            self._logger.debug("Getting static policy versions", policy_id=policy_id)

        response = await self._request(
            "GET",
            f"/api/v1/static-policies/{policy_id}/versions",
        )
        versions = response.get("versions", [])
        return [PolicyVersion.model_validate(v) for v in versions]

    # =========================================================================
    # Policy Override Methods (Enterprise)
    # =========================================================================

    async def create_policy_override(
        self,
        policy_id: str,
        request: CreatePolicyOverrideRequest,
    ) -> PolicyOverride:
        """Create an override for a static policy.

        Args:
            policy_id: ID of the policy to override
            request: Override configuration

        Returns:
            The created override

        Example:
            >>> override = await client.create_policy_override(
            ...     "pol_123",
            ...     CreatePolicyOverrideRequest(
            ...         action=OverrideAction.WARN,
            ...         reason="Temporarily relaxing for migration"
            ...     )
            ... )
        """
        if self._config.debug:
            self._logger.debug(
                "Creating policy override",
                policy_id=policy_id,
                action=request.action_override.value,
            )

        response = await self._request(
            "POST",
            f"/api/v1/static-policies/{policy_id}/override",
            json_data=request.model_dump(mode="json", exclude_none=True, by_alias=True),
        )
        return PolicyOverride.model_validate(response)

    async def delete_policy_override(self, policy_id: str) -> None:
        """Delete an override for a static policy.

        Args:
            policy_id: ID of the policy whose override to delete
        """
        if self._config.debug:
            self._logger.debug("Deleting policy override", policy_id=policy_id)

        await self._request("DELETE", f"/api/v1/static-policies/{policy_id}/override")

    async def list_policy_overrides(self) -> list[PolicyOverride]:
        """List all active policy overrides (Enterprise).

        Returns:
            List of all active policy overrides

        Example:
            >>> overrides = await client.list_policy_overrides()
            >>> for override in overrides:
            ...     print(f"{override.policy_id}: {override.action_override}")
        """
        if self._config.debug:
            self._logger.debug("Listing policy overrides")

        response = await self._request("GET", "/api/v1/static-policies/overrides")
        # Handle both array and wrapped response formats
        # API may return list directly despite _request return type annotation
        if isinstance(response, list):  # type: ignore[unreachable]
            return [PolicyOverride.model_validate(item) for item in response]  # type: ignore[unreachable]
        # Fallback for wrapped response: {"overrides": [...], "count": N}
        overrides = response.get("overrides", [])
        return [PolicyOverride.model_validate(item) for item in overrides]

    # =========================================================================
    # Dynamic Policy Methods
    # =========================================================================

    async def list_dynamic_policies(
        self,
        options: ListDynamicPoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """List all dynamic policies with optional filtering.

        Args:
            options: Filtering and pagination options

        Returns:
            List of dynamic policies
        """
        params: list[str] = []
        if options:
            if options.type:
                params.append(f"type={options.type}")
            if options.tier:
                params.append(f"tier={options.tier.value}")
            if options.organization_id:
                params.append(f"organization_id={options.organization_id}")
            if options.enabled is not None:
                params.append(f"enabled={str(options.enabled).lower()}")
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")
            if options.sort_by:
                params.append(f"sort_by={options.sort_by}")
            if options.sort_order:
                params.append(f"sort_order={options.sort_order}")
            if options.search:
                params.append(f"search={options.search}")

        path = "/api/v1/dynamic-policies"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing dynamic policies", path=path)

        response = await self._orchestrator_request("GET", path)
        policies = response.get("policies") if isinstance(response, dict) else response
        return [DynamicPolicy.model_validate(p) for p in (policies or [])]

    async def get_dynamic_policy(self, policy_id: str) -> DynamicPolicy:
        """Get a specific dynamic policy by ID.

        Args:
            policy_id: Policy ID

        Returns:
            The dynamic policy
        """
        if self._config.debug:
            self._logger.debug("Getting dynamic policy", policy_id=policy_id)

        response = await self._orchestrator_request("GET", f"/api/v1/dynamic-policies/{policy_id}")
        # Response may be wrapped in {"policy": {...}}
        policy_data = response.get("policy", response) if isinstance(response, dict) else response
        return DynamicPolicy.model_validate(policy_data)

    async def create_dynamic_policy(
        self,
        request: CreateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Create a new dynamic policy.

        Args:
            request: Policy creation request

        Returns:
            The created policy
        """
        if self._config.debug:
            self._logger.debug("Creating dynamic policy", name=request.name)

        response = await self._orchestrator_request(
            "POST",
            "/api/v1/dynamic-policies",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        # Response may be wrapped in {"policy": {...}}
        policy_data = response.get("policy", response) if isinstance(response, dict) else response
        return DynamicPolicy.model_validate(policy_data)

    async def update_dynamic_policy(
        self,
        policy_id: str,
        request: UpdateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Update an existing dynamic policy.

        Args:
            policy_id: Policy ID
            request: Fields to update

        Returns:
            The updated policy
        """
        if self._config.debug:
            self._logger.debug("Updating dynamic policy", policy_id=policy_id)

        response = await self._orchestrator_request(
            "PUT",
            f"/api/v1/dynamic-policies/{policy_id}",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        # Response may be wrapped in {"policy": {...}}
        policy_data = response.get("policy", response) if isinstance(response, dict) else response
        return DynamicPolicy.model_validate(policy_data)

    async def delete_dynamic_policy(self, policy_id: str) -> None:
        """Delete a dynamic policy.

        Args:
            policy_id: Policy ID
        """
        if self._config.debug:
            self._logger.debug("Deleting dynamic policy", policy_id=policy_id)

        await self._orchestrator_request("DELETE", f"/api/v1/dynamic-policies/{policy_id}")

    async def toggle_dynamic_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> DynamicPolicy:
        """Toggle a dynamic policy's enabled status.

        Args:
            policy_id: Policy ID
            enabled: Whether the policy should be enabled

        Returns:
            The updated policy
        """
        if self._config.debug:
            self._logger.debug("Toggling dynamic policy", policy_id=policy_id, enabled=enabled)

        response = await self._orchestrator_request(
            "PUT",
            f"/api/v1/dynamic-policies/{policy_id}",
            json_data={"enabled": enabled},
        )
        # Response may be wrapped in {"policy": {...}}
        policy_data = response.get("policy", response) if isinstance(response, dict) else response
        return DynamicPolicy.model_validate(policy_data)

    async def get_effective_dynamic_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """Get effective dynamic policies with tier inheritance applied.

        Args:
            options: Filtering options

        Returns:
            List of effective dynamic policies
        """
        query_params: list[str] = []
        if options:
            if options.category:
                query_params.append(f"category={options.category.value}")
            if options.include_disabled:
                query_params.append("include_disabled=true")

        path = "/api/v1/dynamic-policies/effective"
        if query_params:
            path = f"{path}?{'&'.join(query_params)}"

        if self._config.debug:
            self._logger.debug("Getting effective dynamic policies", path=path)

        response = await self._orchestrator_request("GET", path)
        policies = response.get("policies") if isinstance(response, dict) else response
        return [DynamicPolicy.model_validate(p) for p in (policies or [])]

    # =========================================================================
    # Code Governance Methods (Enterprise)
    # =========================================================================

    async def validate_git_provider(
        self,
        request: ValidateGitProviderRequest,
    ) -> ValidateGitProviderResponse:
        """Validate Git provider credentials before configuration.

        Use this to verify tokens and connectivity before saving.

        Args:
            request: Validation request with provider type and credentials

        Returns:
            Validation result indicating if credentials are valid

        Example:
            >>> result = await client.validate_git_provider(
            ...     ValidateGitProviderRequest(
            ...         type=GitProviderType.GITHUB,
            ...         token="ghp_xxxxxxxxxxxx"
            ...     )
            ... )
            >>> if result.valid:
            ...     print("Credentials are valid")
        """
        if self._config.debug:
            self._logger.debug("Validating Git provider", provider_type=request.type.value)

        response = await self._portal_request(
            "POST",
            "/api/v1/code-governance/git-providers/validate",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return ValidateGitProviderResponse.model_validate(response)

    async def configure_git_provider(
        self,
        request: ConfigureGitProviderRequest,
    ) -> ConfigureGitProviderResponse:
        """Configure a Git provider for code governance.

        Supports GitHub, GitLab, and Bitbucket (cloud and self-hosted).

        Args:
            request: Configuration request with provider type and credentials

        Returns:
            Configuration result

        Example:
            >>> # Configure GitHub with PAT
            >>> await client.configure_git_provider(
            ...     ConfigureGitProviderRequest(
            ...         type=GitProviderType.GITHUB,
            ...         token="ghp_xxxxxxxxxxxx"
            ...     )
            ... )
            >>> # Configure GitLab self-hosted
            >>> await client.configure_git_provider(
            ...     ConfigureGitProviderRequest(
            ...         type=GitProviderType.GITLAB,
            ...         token="glpat-xxxxxxxxxxxx",
            ...         base_url="https://gitlab.mycompany.com"
            ...     )
            ... )
        """
        if self._config.debug:
            self._logger.debug("Configuring Git provider", provider_type=request.type.value)

        response = await self._portal_request(
            "POST",
            "/api/v1/code-governance/git-providers",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return ConfigureGitProviderResponse.model_validate(response)

    async def list_git_providers(self) -> ListGitProvidersResponse:
        """List all configured Git providers for the tenant.

        Returns:
            List of configured providers

        Example:
            >>> result = await client.list_git_providers()
            >>> for provider in result.providers:
            ...     print(f"  - {provider.type.value}")
        """
        if self._config.debug:
            self._logger.debug("Listing Git providers")

        response = await self._portal_request("GET", "/api/v1/code-governance/git-providers")
        return ListGitProvidersResponse.model_validate(response)

    async def delete_git_provider(self, provider_type: GitProviderType) -> None:
        """Delete a configured Git provider.

        Args:
            provider_type: Provider type to delete
        """
        if self._config.debug:
            self._logger.debug("Deleting Git provider", provider_type=provider_type.value)

        path = f"/api/v1/code-governance/git-providers/{provider_type.value}"
        await self._portal_request("DELETE", path)

    async def create_pr(self, request: CreatePRRequest) -> CreatePRResponse:
        """Create a Pull Request from LLM-generated code.

        This creates a PR with full audit trail linking back to the AI request.

        Args:
            request: PR creation request with repository info and files

        Returns:
            Created PR details including URL and number

        Example:
            >>> pr = await client.create_pr(
            ...     CreatePRRequest(
            ...         owner="myorg",
            ...         repo="myrepo",
            ...         title="feat: add user validation utilities",
            ...         files=[
            ...             CodeFile(
            ...                 path="src/utils/validation.py",
            ...                 content=generated_code,
            ...                 language="python",
            ...                 action=FileAction.CREATE
            ...             )
            ...         ],
            ...         agent_request_id="req_123",
            ...         model="gpt-4"
            ...     )
            ... )
            >>> print(f"PR created: {pr.pr_url}")
        """
        if self._config.debug:
            self._logger.debug(
                "Creating PR",
                owner=request.owner,
                repo=request.repo,
                title=request.title,
            )

        response = await self._portal_request(
            "POST",
            "/api/v1/code-governance/prs",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return CreatePRResponse.model_validate(response)

    async def list_prs(
        self,
        options: ListPRsOptions | None = None,
    ) -> ListPRsResponse:
        """List Pull Requests created through code governance.

        Args:
            options: Filtering and pagination options

        Returns:
            List of PR records

        Example:
            >>> result = await client.list_prs(ListPRsOptions(state="open", limit=10))
            >>> for pr in result.prs:
            ...     print(f"#{pr.pr_number}: {pr.title}")
        """
        params: list[str] = []
        if options:
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")
            if options.state:
                params.append(f"state={options.state}")

        path = "/api/v1/code-governance/prs"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing PRs", path=path)

        response = await self._portal_request("GET", path)
        return ListPRsResponse.model_validate(response)

    async def get_pr(self, pr_id: str) -> PRRecord:
        """Get a specific PR record by ID.

        Args:
            pr_id: PR record ID (internal ID, not GitHub PR number)

        Returns:
            PR record details
        """
        if self._config.debug:
            self._logger.debug("Getting PR", pr_id=pr_id)

        response = await self._portal_request("GET", f"/api/v1/code-governance/prs/{pr_id}")
        return PRRecord.model_validate(response)

    async def sync_pr_status(self, pr_id: str) -> PRRecord:
        """Sync PR status with the Git provider.

        This updates the local record with the current state from
        GitHub/GitLab/Bitbucket.

        Args:
            pr_id: PR record ID

        Returns:
            Updated PR record
        """
        if self._config.debug:
            self._logger.debug("Syncing PR status", pr_id=pr_id)

        response = await self._portal_request("POST", f"/api/v1/code-governance/prs/{pr_id}/sync")
        return PRRecord.model_validate(response)

    async def close_pr(self, pr_id: str, delete_branch: bool = True) -> PRRecord:
        """Close a PR without merging and optionally delete the branch.

        This is an enterprise feature for cleaning up test/demo PRs.
        Supports all Git providers: GitHub, GitLab, Bitbucket.

        Args:
            pr_id: PR record ID
            delete_branch: Whether to delete the source branch (default: True)

        Returns:
            Closed PR record
        """
        if self._config.debug:
            self._logger.debug("Closing PR", pr_id=pr_id, delete_branch=delete_branch)

        path = f"/api/v1/code-governance/prs/{pr_id}"
        if delete_branch:
            path += "?delete_branch=true"

        response = await self._portal_request("DELETE", path)
        return PRRecord.model_validate(response)

    # =========================================================================
    # Code Governance Metrics and Export
    # =========================================================================

    async def get_code_governance_metrics(self) -> CodeGovernanceMetrics:
        """Get aggregated code governance metrics.

        Returns PR counts, file totals, and security findings for
        the tenant.

        Returns:
            CodeGovernanceMetrics: Aggregated metrics

        Example:
            >>> metrics = await client.get_code_governance_metrics()
            >>> print(f"Total PRs: {metrics.total_prs}")
            >>> print(f"Secrets found: {metrics.total_secrets_detected}")
        """
        if self._config.debug:
            self._logger.debug("Getting code governance metrics")

        response = await self._portal_request("GET", "/api/v1/code-governance/metrics")
        return CodeGovernanceMetrics.model_validate(response)

    async def export_code_governance_data(
        self,
        options: ExportOptions | None = None,
    ) -> ExportResponse:
        """Export code governance data for compliance reporting.

        Supports JSON format with optional date filtering.

        Args:
            options: Export options (date filters, state filter)

        Returns:
            ExportResponse: Exported PR records

        Example:
            >>> # Export all data
            >>> result = await client.export_code_governance_data()
            >>> print(f"Exported {result.count} records")
            >>>
            >>> # Export with filters
            >>> from datetime import datetime
            >>> from axonflow import ExportOptions
            >>> result = await client.export_code_governance_data(ExportOptions(
            ...     start_date=datetime(2024, 1, 1),
            ...     state="merged"
            ... ))
        """
        query_params: list[str] = ["format=json"]

        if options:
            if options.start_date:
                query_params.append(f"start_date={options.start_date.isoformat()}")
            if options.end_date:
                query_params.append(f"end_date={options.end_date.isoformat()}")
            if options.state:
                query_params.append(f"state={options.state}")

        path = f"/api/v1/code-governance/export?{'&'.join(query_params)}"

        if self._config.debug:
            self._logger.debug("Exporting code governance data", path=path)

        response = await self._portal_request("GET", path)
        return ExportResponse.model_validate(response)

    async def export_code_governance_data_csv(
        self,
        options: ExportOptions | None = None,
    ) -> str:
        """Export code governance data as CSV for compliance reporting.

        Returns raw CSV data suitable for saving to file or streaming.

        Args:
            options: Export options (date filters, state filter)

        Returns:
            str: CSV formatted data

        Example:
            >>> csv_data = await client.export_code_governance_data_csv()
            >>> with open("pr-audit.csv", "w") as f:
            ...     f.write(csv_data)
        """
        query_params: list[str] = ["format=csv"]

        if options:
            if options.start_date:
                query_params.append(f"start_date={options.start_date.isoformat()}")
            if options.end_date:
                query_params.append(f"end_date={options.end_date.isoformat()}")
            if options.state:
                query_params.append(f"state={options.state}")

        path = f"/api/v1/code-governance/export?{'&'.join(query_params)}"

        if self._config.debug:
            self._logger.debug("Exporting code governance data as CSV", path=path)

        return await self._portal_request_text("GET", path)

    # =========================================================================
    # Execution Replay Methods
    # =========================================================================

    async def login_to_portal(self, org_id: str, password: str) -> dict[str, Any]:
        """Login to Customer Portal and store session cookie.

        Required before using Code Governance methods.

        Args:
            org_id: Organization ID
            password: Organization password

        Returns:
            Login response with session info

        Example:
            >>> login = await client.login_to_portal("test-org-001", "test123")
            >>> print(f"Logged in as {login['name']}")
        """
        base_url = self._config.endpoint
        url = f"{base_url}/api/v1/auth/login"

        try:
            response = await self._http_client.post(
                url,
                json={"org_id": org_id, "password": password},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = f"Login failed: HTTP {e.response.status_code}: {e.response.text}"
            raise AuthenticationError(msg) from e
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Customer Portal: {e}"
            raise ConnectionError(msg) from e

        result: dict[str, Any] = response.json()

        # Extract session cookie
        for cookie in response.cookies.jar:
            if cookie.name == "axonflow_session":
                self._session_cookie = cookie.value
                break

        # Fallback to session_id in response body
        if not self._session_cookie and "session_id" in result:
            self._session_cookie = result["session_id"]

        if self._config.debug:
            self._logger.info("Portal login successful", org_id=org_id)

        return result

    async def logout_from_portal(self) -> None:
        """Logout from Customer Portal and clear session cookie."""
        if not self._session_cookie:
            return

        base_url = self._config.endpoint
        url = f"{base_url}/api/v1/auth/logout"

        with contextlib.suppress(httpx.HTTPError):
            await self._http_client.post(
                url,
                cookies={"axonflow_session": self._session_cookie},
            )

        self._session_cookie = None

        if self._config.debug:
            self._logger.info("Portal logout successful")

    def is_logged_in(self) -> bool:
        """Check if logged in to Customer Portal."""
        return self._session_cookie is not None

    async def _portal_request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Make HTTP request to Customer Portal (for enterprise features).

        Requires prior authentication via login_to_portal().
        """
        if not self._session_cookie:
            msg = "Not logged in to Customer Portal. Call login_to_portal() first."
            raise AuthenticationError(msg)

        base_url = self._config.endpoint
        url = f"{base_url}{path}"

        try:
            if self._config.debug:
                self._logger.debug("Portal request", method=method, path=path)

            response = await self._http_client.request(
                method,
                url,
                json=json_data,
                cookies={"axonflow_session": self._session_cookie},
            )
            response.raise_for_status()
            if response.status_code == 204:  # noqa: PLR2004
                return None
            result: dict[str, Any] | list[Any] = response.json()
            return result  # noqa: TRY300

        except httpx.ConnectError as e:
            msg = f"Failed to connect to Customer Portal: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

    async def _portal_request_text(
        self,
        method: str,
        path: str,
    ) -> str:
        """Make HTTP request to Customer Portal and return raw text response.

        Used for CSV exports and other non-JSON responses.
        Requires prior authentication via login_to_portal().
        """
        if not self._session_cookie:
            msg = "Not logged in to Customer Portal. Call login_to_portal() first."
            raise AuthenticationError(msg)

        base_url = self._config.endpoint
        url = f"{base_url}{path}"

        if self._config.debug:
            self._logger.debug("Portal request (text)", method=method, path=path)

        try:
            response = await self._http_client.request(
                method,
                url,
                cookies={"axonflow_session": self._session_cookie},
            )
            response.raise_for_status()
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Customer Portal: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

        return response.text

    async def _orchestrator_request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        scoped_resource: str | None = None,
        scoped_identifier: str | None = None,
        scoped_page_key: str | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Make HTTP request to Orchestrator.

        ``scoped_resource``/``scoped_identifier`` opt a route into the
        read-scope diagnosis: a 404 on a role-scoped read is re-raised as
        :class:`~axonflow.read_identity.ReadScopeError` when the platform's
        ``X-Axonflow-Read-Scope`` header says the caller's scope decided it.
        Routes that pass neither keep the plain ``AxonFlowError``.

        ``scoped_page_key`` additionally opts a LIST route into the
        empty-page refusal. The count is taken here, where the response
        object still exists, and it handles both shapes the platform sends —
        a bare array, or an envelope whose rows live under that key. Doing it
        per caller would put one rule in two places per method, and the rule
        would end up holding on whichever branch the server happened to take.
        """
        self._pre_request_hook()
        base_url = self._config.endpoint
        url = f"{base_url}{path}"

        try:
            response = await self._http_client.request(method, url, json=json_data)
            response.raise_for_status()
            if response.status_code == 204:  # noqa: PLR2004
                return None
            result: dict[str, Any] | list[Any] = response.json()
            if scoped_page_key is not None:
                # The two shapes the platform sends. `result` is typed as
                # dict | list, so there is no third arm to write — an `else:
                # rows = 0` here is provably dead and mypy says so.
                if isinstance(result, list):
                    rows = len(result)
                else:
                    entries = result.get(scoped_page_key)
                    rows = len(entries) if isinstance(entries, list) else 0
                scoped = refuse_vacuous_scoped_page(response, scoped_resource or "read", rows)
                if scoped is not None:
                    raise scoped
            return result  # noqa: TRY300

        except httpx.ConnectError as e:
            msg = f"Failed to connect to Orchestrator: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            # A scoped miss reports WHY it missed. Only 404 is interpreted: the
            # scope header is stamped before the handler writes its status, so
            # it also rides a 500 from further down the handler, and explaining
            # a server fault as a scoping outcome would be exactly the
            # confidently-wrong diagnosis this type exists to prevent.
            if scoped_resource is not None and e.response.status_code == 404:  # noqa: PLR2004
                scoped = read_scope_error_for(
                    resource=scoped_resource,
                    identifier=scoped_identifier,
                    scope=read_scope_of(e.response),
                    status_code=e.response.status_code,
                )
                if scoped is not None:
                    raise scoped from e
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

    async def list_executions(
        self,
        options: ListExecutionsOptions | None = None,
    ) -> ListExecutionsResponse:
        """List workflow executions with optional filtering.

        Args:
            options: Filtering and pagination options

        Returns:
            ListExecutionsResponse with executions and pagination info

        Example:
            >>> result = await client.list_executions(
            ...     ListExecutionsOptions(status="completed", limit=10)
            ... )
            >>> for exec in result.executions:
            ...     print(f"{exec.request_id}: {exec.status}")
        """
        params: list[str] = []
        if options:
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")
            if options.status:
                params.append(f"status={options.status}")
            if options.workflow_id:
                params.append(f"workflow_id={options.workflow_id}")
            if options.start_time:
                params.append(f"start_time={options.start_time.isoformat()}")
            if options.end_time:
                params.append(f"end_time={options.end_time.isoformat()}")

        path = "/api/v1/executions"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing executions", path=path)

        response = await self._orchestrator_request("GET", path)
        return ListExecutionsResponse.model_validate(response)

    async def get_execution(self, execution_id: str) -> ExecutionDetail:
        """Get a complete execution record including summary and all steps.

        Args:
            execution_id: Execution/request ID

        Returns:
            ExecutionDetail with summary and steps

        Example:
            >>> execution = await client.get_execution("exec-abc123")
            >>> print(f"Status: {execution.summary.status}")
            >>> for step in execution.steps:
            ...     print(f"  Step {step.step_index}: {step.step_name}")
        """
        if self._config.debug:
            self._logger.debug("Getting execution", execution_id=execution_id)

        response = await self._orchestrator_request("GET", f"/api/v1/executions/{execution_id}")
        return ExecutionDetail.model_validate(response)

    async def get_execution_steps(self, execution_id: str) -> list[ExecutionSnapshot]:
        """Get all step snapshots for an execution.

        Args:
            execution_id: Execution/request ID

        Returns:
            List of step snapshots

        Example:
            >>> steps = await client.get_execution_steps("exec-abc123")
            >>> for step in steps:
            ...     print(f"Step {step.step_index}: {step.status}")
        """
        if self._config.debug:
            self._logger.debug("Getting execution steps", execution_id=execution_id)

        path = f"/api/v1/executions/{execution_id}/steps"
        response = await self._orchestrator_request("GET", path)
        if response is None:
            return []
        return [ExecutionSnapshot.model_validate(s) for s in response]

    async def get_execution_timeline(self, execution_id: str) -> list[TimelineEntry]:
        """Get timeline view of execution for visualization.

        Args:
            execution_id: Execution/request ID

        Returns:
            List of timeline entries

        Example:
            >>> timeline = await client.get_execution_timeline("exec-abc123")
            >>> for entry in timeline:
            ...     status = f" [ERROR]" if entry.has_error else ""
            ...     print(f"[{entry.step_index}] {entry.step_name}: {entry.status}{status}")
        """
        if self._config.debug:
            self._logger.debug("Getting execution timeline", execution_id=execution_id)

        path = f"/api/v1/executions/{execution_id}/timeline"
        response = await self._orchestrator_request("GET", path)
        if response is None:
            return []
        return [TimelineEntry.model_validate(e) for e in response]

    async def export_execution(
        self,
        execution_id: str,
        options: ExecutionExportOptions | None = None,
    ) -> dict[str, Any]:
        """Export a complete execution record for compliance or archival.

        Args:
            execution_id: Execution/request ID
            options: Export options (format, what to include)

        Returns:
            Exported execution data

        Example:
            >>> export = await client.export_execution(
            ...     "exec-abc123",
            ...     ExecutionExportOptions(include_input=True, include_output=True)
            ... )
            >>> import json
            >>> with open("audit-export.json", "w") as f:
            ...     json.dump(export, f, indent=2)
        """
        params: list[str] = []
        if options:
            if options.format:
                params.append(f"format={options.format}")
            if options.include_input:
                params.append("include_input=true")
            if options.include_output:
                params.append("include_output=true")
            if options.include_policies:
                params.append("include_policies=true")

        path = f"/api/v1/executions/{execution_id}/export"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Exporting execution", execution_id=execution_id)

        return await self._orchestrator_request("GET", path)  # type: ignore[return-value]

    async def delete_execution(self, execution_id: str) -> None:
        """Delete an execution and all associated step snapshots.

        Args:
            execution_id: Execution/request ID

        Example:
            >>> await client.delete_execution("exec-abc123")
        """
        if self._config.debug:
            self._logger.debug("Deleting execution", execution_id=execution_id)

        await self._orchestrator_request("DELETE", f"/api/v1/executions/{execution_id}")

    # ========================================
    # COST CONTROLS - BUDGETS
    # ========================================

    async def create_budget(self, request: CreateBudgetRequest) -> Budget:
        """Create a new budget.

        Args:
            request: Budget creation request

        Returns:
            The created budget

        Example:
            >>> budget = await client.create_budget(CreateBudgetRequest(
            ...     id="my-budget",
            ...     name="Monthly Budget",
            ...     scope=BudgetScope.ORGANIZATION,
            ...     limit_usd=100.0,
            ...     period=BudgetPeriod.MONTHLY,
            ...     on_exceed=BudgetOnExceed.WARN,
            ...     alert_thresholds=[50, 80, 100]
            ... ))
        """

        response = await self._orchestrator_request(
            "POST", "/api/v1/budgets", json_data=request.model_dump(exclude_none=True)
        )
        return Budget.model_validate(response)

    async def get_budget(self, budget_id: str) -> Budget:
        """Get a budget by ID.

        Args:
            budget_id: Budget ID

        Returns:
            The budget
        """

        response = await self._orchestrator_request("GET", f"/api/v1/budgets/{budget_id}")
        return Budget.model_validate(response)

    async def list_budgets(self, options: ListBudgetsOptions | None = None) -> BudgetsResponse:
        """List all budgets.

        Args:
            options: Filtering and pagination options

        Returns:
            List of budgets
        """

        params: list[str] = []
        if options:
            if options.scope:
                params.append(f"scope={options.scope.value}")
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")

        path = "/api/v1/budgets"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._orchestrator_request("GET", path)
        return BudgetsResponse.model_validate(response)

    async def update_budget(self, budget_id: str, request: UpdateBudgetRequest) -> Budget:
        """Update an existing budget.

        Args:
            budget_id: Budget ID
            request: Update request

        Returns:
            The updated budget
        """

        response = await self._orchestrator_request(
            "PUT",
            f"/api/v1/budgets/{budget_id}",
            json_data=request.model_dump(exclude_none=True),
        )
        return Budget.model_validate(response)

    async def delete_budget(self, budget_id: str) -> None:
        """Delete a budget.

        Args:
            budget_id: Budget ID
        """
        await self._orchestrator_request("DELETE", f"/api/v1/budgets/{budget_id}")

    # ========================================
    # COST CONTROLS - BUDGET STATUS & ALERTS
    # ========================================

    async def get_budget_status(self, budget_id: str) -> BudgetStatus:
        """Get the current status of a budget.

        Args:
            budget_id: Budget ID

        Returns:
            Budget status including usage and remaining amount
        """

        response = await self._orchestrator_request("GET", f"/api/v1/budgets/{budget_id}/status")
        return BudgetStatus.model_validate(response)

    async def get_budget_alerts(self, budget_id: str) -> BudgetAlertsResponse:
        """Get alerts for a budget.

        Args:
            budget_id: Budget ID

        Returns:
            Budget alerts
        """

        response = await self._orchestrator_request("GET", f"/api/v1/budgets/{budget_id}/alerts")
        return BudgetAlertsResponse.model_validate(response)

    async def check_budget(self, request: BudgetCheckRequest) -> BudgetDecision:
        """Perform a pre-flight budget check.

        Args:
            request: Check request with scope IDs

        Returns:
            Budget decision
        """

        response = await self._orchestrator_request(
            "POST", "/api/v1/budgets/check", json_data=request.model_dump(exclude_none=True)
        )
        return BudgetDecision.model_validate(response)

    # ========================================
    # COST CONTROLS - USAGE
    # ========================================

    async def get_usage_summary(self, period: str | None = None) -> UsageSummary:
        """Get usage summary for a period.

        Args:
            period: Period (daily, weekly, monthly, quarterly, yearly)

        Returns:
            Usage summary
        """

        path = "/api/v1/usage"
        if period:
            path = f"{path}?period={period}"

        response = await self._orchestrator_request("GET", path)
        return UsageSummary.model_validate(response)

    async def get_usage_breakdown(self, group_by: str, period: str | None = None) -> UsageBreakdown:
        """Get usage breakdown by a grouping dimension.

        Args:
            group_by: Dimension to group by (provider, model, agent, team, workflow)
            period: Period (daily, weekly, monthly, quarterly, yearly)

        Returns:
            Usage breakdown
        """

        params: list[str] = [f"group_by={group_by}"]
        if period:
            params.append(f"period={period}")

        path = f"/api/v1/usage/breakdown?{'&'.join(params)}"
        response = await self._orchestrator_request("GET", path)
        return UsageBreakdown.model_validate(response)

    async def list_usage_records(
        self, options: ListUsageRecordsOptions | None = None
    ) -> UsageRecordsResponse:
        """List usage records.

        Args:
            options: Filtering and pagination options

        Returns:
            List of usage records
        """

        params: list[str] = []
        if options:
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")
            if options.provider:
                params.append(f"provider={options.provider}")
            if options.model:
                params.append(f"model={options.model}")

        path = "/api/v1/usage/records"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._orchestrator_request("GET", path)
        return UsageRecordsResponse.model_validate(response)

    # ========================================
    # COST CONTROLS - PRICING
    # ========================================

    async def get_pricing(
        self, provider: str | None = None, model: str | None = None
    ) -> PricingListResponse:
        """Get pricing information for models.

        Args:
            provider: Filter by provider (optional)
            model: Filter by model (optional)

        Returns:
            Pricing information
        """

        params: list[str] = []
        if provider:
            params.append(f"provider={provider}")
        if model:
            params.append(f"model={model}")

        path = "/api/v1/pricing"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._orchestrator_request("GET", path)

        # Handle single object vs array response
        if isinstance(response, dict) and "provider" in response:
            # Single object response - wrap in list
            return PricingListResponse(pricing=[PricingInfo.model_validate(response)])
        return PricingListResponse.model_validate(response)

    # ========================================
    # WORKFLOW CONTROL PLANE
    # ========================================
    # The Workflow Control Plane provides governance gates for external
    # orchestrators like LangChain, LangGraph, and CrewAI.
    #
    # "LangChain runs the workflow. AxonFlow decides when it's allowed to move forward."
    #
    # Usage:
    #   1. Call create_workflow() to register a new workflow
    #   2. Before each step, call step_gate() to check if the step is allowed
    #   3. If decision is 'block', stop the workflow
    #   4. If decision is 'require_approval', wait for approval
    #   5. After each step, optionally call mark_step_completed()
    #   6. Call complete_workflow() or abort_workflow() when done

    async def create_workflow(
        self,
        request: CreateWorkflowRequest,
    ) -> CreateWorkflowResponse:
        """Create a new workflow for governance tracking.

        Registers a new workflow with AxonFlow. Call this at the start of your
        external orchestrator workflow (LangChain, LangGraph, CrewAI, etc.).

        Args:
            request: Workflow creation request

        Returns:
            Created workflow with ID

        Example:
            >>> workflow = await client.create_workflow(
            ...     CreateWorkflowRequest(
            ...         workflow_name="customer-support-agent",
            ...         source=WorkflowSource.LANGGRAPH,
            ...         metadata={"customer_id": "cust-123"}
            ...     )
            ... )
            >>> print(f"Workflow created: {workflow.workflow_id}")
        """
        body = {
            "workflow_name": request.workflow_name,
            "source": request.source.value if request.source else "external",
            "metadata": request.metadata,
        }
        if request.trace_id:
            body["trace_id"] = request.trace_id

        if self._config.debug:
            self._logger.debug("Creating workflow", workflow_name=request.workflow_name)

        response = await self._orchestrator_request("POST", "/api/v1/workflows", json_data=body)
        if not isinstance(response, dict):
            msg = "Unexpected response type from workflow creation"
            raise TypeError(msg)

        # The wire emits `started_at` (canonical) — `created_at` is a
        # legacy SDK field that has always read None. Read both for
        # back-compat: prefer the canonical value, fall through to the
        # legacy slot only if the canonical key is missing entirely.
        # `is not None` (not `or`) so an empty string from a buggy
        # server doesn't silently swap to the legacy slot.
        started_at_raw = response.get("started_at")
        if started_at_raw is None:
            started_at_raw = response.get("created_at")
        started_at = _parse_datetime(started_at_raw) if started_at_raw else None
        # The wire shape doesn't include `source` on the create
        # response; if a legacy server still sends it, surface it.
        source_str = response.get("source")
        return CreateWorkflowResponse(
            workflow_id=response["workflow_id"],
            workflow_name=response["workflow_name"],
            status=WorkflowStatus(response["status"]),
            started_at=started_at,
            trace_id=response.get("trace_id"),
            # @deprecated aliases populated for source-compat with
            # callers that read the legacy field names.
            created_at=started_at,
            source=WorkflowSource(source_str) if source_str else None,
        )

    async def get_workflow(self, workflow_id: str) -> WorkflowStatusResponse:
        """Get the status of a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            Workflow status including steps

        Example:
            >>> status = await client.get_workflow("wf_123")
            >>> print(f"Status: {status.status}, Step: {status.current_step_index}")
        """
        response = await self._orchestrator_request("GET", f"/api/v1/workflows/{workflow_id}")
        if not isinstance(response, dict):
            msg = "Unexpected response type from get workflow"
            raise TypeError(msg)
        return self._map_workflow_response(response)

    async def step_gate(
        self,
        workflow_id: str,
        step_id: str,
        request: StepGateRequest,
        *,
        include_prior_output: bool = False,
    ) -> StepGateResponse:
        """Check if a workflow step is allowed to proceed (step gate).

        This is the core governance method. Call this before executing each step
        in your workflow to check if the step is allowed based on policies.

        Args:
            workflow_id: Workflow ID
            step_id: Unique step identifier (you provide this)
            request: Step gate request with step details
            include_prior_output: When True, sends ``?include_prior_output=true`` and
                ``retry_context.prior_output`` is populated when a prior /complete has
                landed. Default False because prior output may be large and/or contain
                sensitive data.

        Returns:
            Gate decision: allow, block, or require_approval

        Raises:
            IdempotencyKeyMismatchError: If ``request.idempotency_key`` conflicts with
                the key recorded on an earlier gate call for this (workflow_id, step_id).

        Example:
            >>> gate = await client.step_gate(
            ...     "wf_123",
            ...     "step-generate-code",
            ...     StepGateRequest(
            ...         step_name="Generate Code",
            ...         step_type=StepType.LLM_CALL,
            ...         model="gpt-4",
            ...         provider="openai",
            ...         idempotency_key="payment:wire:acct4471:invoice-7721",
            ...     ),
            ...     include_prior_output=True,
            ... )
            >>> if gate.decision == GateDecision.BLOCK:
            ...     raise Exception(f"Step blocked: {gate.reason}")
            >>> if gate.retry_context and gate.retry_context.prior_completion_status == "completed":
            ...     prior = gate.retry_context.prior_output  # previous result, if any
        """
        body: dict[str, Any] = {
            "step_name": request.step_name,
            "step_type": request.step_type.value,
            "step_input": request.step_input,
            "model": request.model,
            "provider": request.provider,
        }
        if request.tool_context:
            tc: dict[str, Any] = {
                "tool_name": request.tool_context.tool_name,
                "tool_input": request.tool_context.tool_input,
            }
            if request.tool_context.tool_type is not None:
                tc["tool_type"] = request.tool_context.tool_type
            body["tool_context"] = tc
        if request.retry_policy is not None:
            body["retry_policy"] = request.retry_policy.value
        if request.idempotency_key is not None:
            body["idempotency_key"] = request.idempotency_key
        # Wire-canonical budget-hint fields surfaced in the v6 sweep.
        if request.tokens_in is not None:
            body["tokens_in"] = request.tokens_in
        if request.tokens_out is not None:
            body["tokens_out"] = request.tokens_out
        if request.cost_usd is not None:
            body["cost_usd"] = request.cost_usd

        if self._config.debug:
            self._logger.debug(
                "Checking step gate",
                workflow_id=workflow_id,
                step_id=step_id,
                step_type=request.step_type.value,
            )

        path = f"/api/v1/workflows/{workflow_id}/steps/{step_id}/gate"
        if include_prior_output:
            path += "?include_prior_output=true"
        response = await self._step_request_with_idempotency_check(
            path, body, workflow_id=workflow_id, step_id=step_id
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from step gate"
            raise TypeError(msg)

        retry_context = None
        rc_raw = response.get("retry_context")
        if isinstance(rc_raw, dict):
            retry_context = RetryContext.model_validate(rc_raw)

        return StepGateResponse(
            decision=GateDecision(response["decision"]),
            step_id=response["step_id"],
            reason=response.get("reason"),
            policy_ids=response.get("policy_ids", []),
            approval_url=response.get("approval_url"),
            decision_id=response.get("decision_id"),
            policies_evaluated=response.get("policies_evaluated"),
            policies_matched=response.get("policies_matched"),
            cached=response.get("cached", False),
            decision_source=response.get("decision_source"),
            retry_context=retry_context,
        )

    async def mark_step_completed(
        self,
        workflow_id: str,
        step_id: str,
        request: MarkStepCompletedRequest | None = None,
    ) -> None:
        """Mark a step as completed.

        Call this after successfully executing a step to record its completion.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID
            request: Optional completion request with output data

        Raises:
            IdempotencyKeyMismatchError: If ``request.idempotency_key`` does not match the
                key recorded on the earlier gate call for this (workflow_id, step_id).

        Example:
            >>> await client.mark_step_completed(
            ...     "wf_123",
            ...     "step-1",
            ...     MarkStepCompletedRequest(output={"result": "Code generated"})
            ... )
        """
        body: dict[str, Any] = {}
        if request:
            body = {"output": request.output, "metadata": request.metadata}
            if request.tokens_in is not None:
                body["tokens_in"] = request.tokens_in
            if request.tokens_out is not None:
                body["tokens_out"] = request.tokens_out
            if request.cost_usd is not None:
                body["cost_usd"] = request.cost_usd
            if request.idempotency_key is not None:
                body["idempotency_key"] = request.idempotency_key

        await self._step_request_with_idempotency_check(
            f"/api/v1/workflows/{workflow_id}/steps/{step_id}/complete",
            body,
            workflow_id=workflow_id,
            step_id=step_id,
        )

        if self._config.debug:
            self._logger.debug("Step marked completed", workflow_id=workflow_id, step_id=step_id)

    async def _step_request_with_idempotency_check(
        self,
        path: str,
        body: dict[str, Any],
        *,
        workflow_id: str,
        step_id: str,
    ) -> dict[str, Any] | list[Any] | None:
        """POST to a step gate/complete endpoint, mapping 409 IDEMPOTENCY_KEY_MISMATCH to
        IdempotencyKeyMismatchError. All other errors are handled like _orchestrator_request.
        """
        url = f"{self._config.endpoint}{path}"
        try:
            response = await self._http_client.request("POST", url, json=body)
            response.raise_for_status()
            if response.status_code == 204:  # noqa: PLR2004
                return None
            result: dict[str, Any] | list[Any] = response.json()
            return result  # noqa: TRY300
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Orchestrator: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:  # noqa: PLR2004
                idem = _parse_idempotency_key_mismatch(
                    e.response, workflow_id=workflow_id, step_id=step_id
                )
                if idem is not None:
                    raise idem from e
            msg = f"HTTP {e.response.status_code}: {e.response.text}"
            raise AxonFlowError(msg) from e

    async def complete_workflow(self, workflow_id: str) -> None:
        """Complete a workflow successfully.

        Call this when your workflow has completed all steps successfully.

        Args:
            workflow_id: Workflow ID

        Example:
            >>> await client.complete_workflow("wf_123")
        """
        await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/complete",
            json_data={},
        )

        if self._config.debug:
            self._logger.debug("Workflow completed", workflow_id=workflow_id)

    async def abort_workflow(self, workflow_id: str, reason: str | None = None) -> None:
        """Abort a workflow.

        Call this when you need to stop a workflow due to an error or user request.

        Args:
            workflow_id: Workflow ID
            reason: Optional reason for aborting

        Example:
            >>> await client.abort_workflow("wf_123", "User cancelled the operation")
        """
        body = {"reason": reason} if reason else {}

        await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/abort",
            json_data=body,
        )

        if self._config.debug:
            self._logger.debug("Workflow aborted", workflow_id=workflow_id, reason=reason)

    async def fail_workflow(self, workflow_id: str, reason: str | None = None) -> None:
        """Fail a workflow.

        Call this when a workflow has failed due to an unrecoverable error.

        Args:
            workflow_id: Workflow ID
            reason: Optional reason for the failure

        Example:
            >>> await client.fail_workflow("wf_123", "Pipeline stage crashed")
        """
        body = {"reason": reason} if reason else {}

        await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/fail",
            json_data=body,
        )

        if self._config.debug:
            self._logger.debug("Workflow failed", workflow_id=workflow_id, reason=reason)

    async def resume_workflow(self, workflow_id: str) -> None:
        """Resume a workflow after approval.

        Call this after a step has been approved to continue the workflow.

        Args:
            workflow_id: Workflow ID

        Example:
            >>> # After approval received via webhook or polling
            >>> await client.resume_workflow("wf_123")
        """
        await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/resume",
            json_data={},
        )

        if self._config.debug:
            self._logger.debug("Workflow resumed", workflow_id=workflow_id)

    async def get_checkpoints(self, workflow_id: str) -> CheckpointListResponse:
        """List all step-gate checkpoints for a workflow.

        Checkpoints are created automatically at each step gate evaluation.
        Available in all tiers.

        Args:
            workflow_id: Workflow ID

        Returns:
            List of checkpoints ordered by step index

        Example:
            >>> checkpoints = await client.get_checkpoints("wf_123")
            >>> for cp in checkpoints.checkpoints:
            ...     print(f"{cp.step_id}: {cp.gate_decision} (resumable={cp.is_resumable})")
        """
        response = await self._orchestrator_request(
            "GET",
            f"/api/v1/workflows/{workflow_id}/checkpoints",
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from get checkpoints"
            raise TypeError(msg)
        return CheckpointListResponse(**response)

    async def resume_from_last_checkpoint(
        self,
        workflow_id: str,
    ) -> ResumeFromCheckpointResponse:
        """Resume a workflow from its last resumable checkpoint.

        Evaluation+ tier. Re-evaluates with current policies.

        Args:
            workflow_id: Workflow ID

        Returns:
            Resume result with fresh decision
        """
        response = await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/checkpoints/resume",
            json_data={},
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from resume checkpoint"
            raise TypeError(msg)
        return ResumeFromCheckpointResponse(**response)

    async def resume_from_checkpoint(
        self,
        workflow_id: str,
        checkpoint_id: int,
    ) -> ResumeFromCheckpointResponse:
        """Resume a workflow from a specific checkpoint with fresh policy evaluation.

        Enterprise only. The step gate at the checkpoint boundary is re-evaluated
        with current policies.

        Args:
            workflow_id: Workflow ID
            checkpoint_id: Checkpoint database ID

        Returns:
            Resume result with fresh decision

        Example:
            >>> result = await client.resume_from_checkpoint("wf_123", 42)
            >>> print(f"New decision: {result.new_decision}")
        """
        response = await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/checkpoints/{checkpoint_id}/resume",
            json_data={},
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from resume checkpoint"
            raise TypeError(msg)
        return ResumeFromCheckpointResponse(**response)

    async def list_workflows(
        self,
        options: ListWorkflowsOptions | None = None,
    ) -> ListWorkflowsResponse:
        """List workflows with optional filters.

        Args:
            options: Filter and pagination options

        Returns:
            List of workflows

        Example:
            >>> result = await client.list_workflows(
            ...     ListWorkflowsOptions(
            ...         status=WorkflowStatus.IN_PROGRESS,
            ...         source=WorkflowSource.LANGGRAPH,
            ...         limit=10
            ...     )
            ... )
            >>> print(f"Found {result.total} workflows")
        """
        params: list[str] = []
        if options:
            if options.status:
                params.append(f"status={options.status.value}")
            if options.source:
                params.append(f"source={options.source.value}")
            if options.trace_id:
                params.append(f"trace_id={options.trace_id}")
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")

        path = "/api/v1/workflows"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._orchestrator_request("GET", path)
        if not isinstance(response, dict):
            msg = "Unexpected response type from list workflows"
            raise TypeError(msg)

        workflows = [self._map_workflow_response(w) for w in response.get("workflows", [])]

        return ListWorkflowsResponse(
            workflows=workflows,
            total=response.get("total", len(workflows)),
            limit=response.get("limit"),
            offset=response.get("offset"),
        )

    # =========================================================================
    # WCP Approval Methods (Feature 5)
    # =========================================================================

    async def approve_step(
        self,
        workflow_id: str,
        step_id: str,
        comment: str = "",
    ) -> ApproveStepResponse:
        """Approve a workflow step that requires human approval.

        The server requires ``comment`` with a minimum of 10 characters — it's
        the audit-trail justification that every approval carries into the
        workflow history. Callers should always supply a meaningful comment.

        Call this to approve a step that received a ``require_approval`` gate decision.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID to approve
            comment: Audit justification for the approval (min 10 chars server-side)

        Returns:
            ApproveStepResponse with approval confirmation

        Example:
            >>> result = await client.approve_step(
            ...     "wf_123", "step-1", comment="Approved after full audit review"
            ... )
            >>> print(f"Step {result.step_id} status: {result.status}")
        """
        body: dict[str, Any] = {}
        if comment:
            body["comment"] = comment
        response = await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/steps/{step_id}/approve",
            json_data=body,
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from approve step"
            raise TypeError(msg)

        return ApproveStepResponse(
            workflow_id=response.get("workflow_id", workflow_id),
            step_id=response.get("step_id", step_id),
            status=response["status"],
        )

    async def reject_step(
        self,
        workflow_id: str,
        step_id: str,
        reason: str = "",
    ) -> RejectStepResponse:
        """Reject a workflow step that requires human approval.

        Call this to reject a step that received a ``require_approval`` gate decision.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID to reject
            reason: Optional reason for rejection

        Returns:
            RejectStepResponse with rejection confirmation

        Example:
            >>> result = await client.reject_step("wf_123", "step-1", reason="Unsafe operation")
            >>> print(f"Step {result.step_id} status: {result.status}")
        """
        body: dict[str, Any] = {}
        if reason:
            body["reason"] = reason

        response = await self._orchestrator_request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/steps/{step_id}/reject",
            json_data=body,
        )
        if not isinstance(response, dict):
            msg = "Unexpected response type from reject step"
            raise TypeError(msg)

        return RejectStepResponse(
            workflow_id=response.get("workflow_id", workflow_id),
            step_id=response.get("step_id", step_id),
            status=response["status"],
        )

    async def get_pending_approvals(
        self,
        limit: int = 20,
    ) -> PendingApprovalsResponse:
        """Get all pending approvals across workflows — the WCP-plane listing.

        Use :meth:`get_pending_plan_approvals` for the MAP-plane listing
        (scopes to MAP-backed workflows and populates ``plan_id`` on every
        entry).

        Available on Evaluation+ licenses.

        Args:
            limit: Maximum number of pending approvals to return (default: 20)

        Returns:
            PendingApprovalsResponse with list of pending approvals

        Example:
            >>> result = await client.get_pending_approvals(limit=10)
            >>> for approval in result.pending_approvals:
            ...     print(f"{approval.workflow_name}/{approval.step_name}: "
            ...           f"pending since {approval.created_at}")
        """
        path = f"/api/v1/workflows/approvals/pending?limit={limit}"

        response = await self._orchestrator_request("GET", path)
        if not isinstance(response, dict):
            msg = "Unexpected response type from get pending approvals"
            raise TypeError(msg)

        return _parse_pending_approvals_response(response)

    async def get_pending_plan_approvals(
        self,
        limit: int = 20,
        plan_id: str | None = None,
    ) -> PendingApprovalsResponse:
        """List pending approvals for MAP-backed workflows.

        The MAP-plane counterpart of :meth:`get_pending_approvals`. Every
        returned entry has ``plan_id`` populated; WCP-only approvals are not
        returned.

        Requires an Evaluation or Enterprise license (same tier gate as the
        MAP step approve/reject endpoints).

        Args:
            limit: Maximum number of pending approvals to return (default: 20)
            plan_id: Optional plan id — when set, scopes the listing to a
                single plan.

        Returns:
            PendingApprovalsResponse with list of MAP-plane pending approvals

        Example:
            >>> result = await client.get_pending_plan_approvals(plan_id="plan-abc123")
            >>> for approval in result.pending_approvals:
            ...     print(f"Plan {approval.plan_id} step {approval.step_id} awaiting approval")
        """
        query = f"limit={limit}"
        if plan_id:
            query += f"&plan_id={quote(plan_id, safe='')}"
        path = f"/api/v1/plans/approvals/pending?{query}"

        response = await self._orchestrator_request("GET", path)
        if not isinstance(response, dict):
            msg = "Unexpected response type from get pending plan approvals"
            raise TypeError(msg)

        return _parse_pending_approvals_response(response)

    # =========================================================================
    # HITL Queue API (Enterprise)
    # =========================================================================

    async def list_hitl_queue(
        self,
        opts: HITLQueueListOptions | None = None,
    ) -> HITLQueueListResponse:
        """List approval requests in the HITL queue.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Args:
            opts: Optional filtering and pagination options

        Returns:
            HITLQueueListResponse with list of approval requests

        Example:
            >>> result = await client.list_hitl_queue(
            ...     HITLQueueListOptions(status="pending", severity="critical", limit=10)
            ... )
            >>> for item in result.items:
            ...     print(f"{item.request_id}: {item.original_query} [{item.severity}]")
        """
        params: list[str] = []
        if opts:
            if opts.status:
                params.append(f"status={opts.status}")
            if opts.severity:
                params.append(f"severity={opts.severity}")
            if opts.limit is not None:
                params.append(f"limit={opts.limit}")
            if opts.offset is not None:
                params.append(f"offset={opts.offset}")

        path = "/api/v1/hitl/queue"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing HITL queue", path=path)

        response = await self._request("GET", path)
        # Server returns {success, data: [...items], meta: {total, limit, offset}}
        data = response.get("data", []) if isinstance(response, dict) else []
        if isinstance(data, list):
            items = [HITLApprovalRequest.model_validate(item) for item in data]
        else:
            items = []
        meta = response.get("meta", {}) if isinstance(response, dict) else {}
        total = meta.get("total", len(items))
        offset = meta.get("offset", 0)
        return HITLQueueListResponse(
            items=items,
            total=total,
            has_more=(offset + len(items)) < total,
        )

    async def create_hitl_request(
        self,
        request: HITLCreateInput,
    ) -> HITLApprovalRequest:
        """Create a HITL approval request in the queue.

        Enterprise Feature: Requires AxonFlow Enterprise license. The
        platform's ``POST /api/v1/hitl/queue`` handler returns 403 with
        ``ErrHITLApprovalDisabledByTier`` when called against a community
        tier that hasn't enabled HITL, and 401 when credentials are
        invalid.

        This is the explicit row-creation step for callers that detect
        ``require_approval`` from a separate gate (``pre_check``,
        ``check_tool_input``, MAP plan approvals) and want the row enqueued
        so a reviewer can act on it. After creating, poll
        ``get_hitl_request(<returned approval_id>)`` until terminal state,
        or pass ``notify_url`` so the platform fires a signed webhook on
        terminal-state transition (see
        ``axonflow-docs/docs/governance/hitl.md`` for the envelope shape).

        Args:
            request: Pre-populated :class:`HITLCreateInput`. ``client_id``,
                ``original_query``, and ``request_type`` are required; all
                other fields are optional. Bad ``notify_url`` schemes are
                rejected by the platform with HTTP 400 (surfaced here as
                :class:`AxonFlowError`); only ``https://`` (and
                ``http://`` for self-hosted local-dev) are accepted.

        Returns:
            The created :class:`HITLApprovalRequest` with ``request_id``
            populated.

        Raises:
            AuthenticationError: 401 from the platform (invalid creds).
            PolicyViolationError: 403 from the platform (tier gate or
                missing/forbidden org/tenant context).
            AxonFlowError: 400 (validation: bad ``notify_url`` scheme,
                missing required fields), 429 (pending-approval cap), or
                any other non-2xx response.
            ConnectionError: TCP/TLS-level connection failure.
            TimeoutError: Request timed out.

        Example:
            >>> req = await client.create_hitl_request(
            ...     HITLCreateInput(
            ...         client_id="loan-desk",
            ...         original_query="disburse $50000 to cust-001",
            ...         request_type="adk-tool",
            ...         triggered_policy_id="loan-amount-cap",
            ...         triggered_policy_name="Loan amount cap",
            ...         trigger_reason="Disbursement above $10k requires manager approval",
            ...         severity="high",
            ...         notify_url="https://workflows.example.com/hooks/loan-approve",
            ...     )
            ... )
            >>> print(req.request_id)
        """
        body = request.model_dump(exclude_none=True)

        if self._config.debug:
            self._logger.debug(
                "Creating HITL request",
                client_id=request.client_id,
                request_type=request.request_type,
                notify_url=request.notify_url,
            )

        response = await self._request("POST", "/api/v1/hitl/queue", json_data=body)
        # Server returns {success, data: <HITLApprovalRequest>} per
        # `APIResponse` in platform/agent/hitl/handler.go:118.
        data = response.get("data", response) if isinstance(response, dict) else response
        return HITLApprovalRequest.model_validate(data)

    async def get_hitl_request(self, request_id: str) -> HITLApprovalRequest:
        """Get a specific HITL approval request.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Args:
            request_id: ID of the approval request

        Returns:
            The HITL approval request

        Example:
            >>> request = await client.get_hitl_request("hitl-req-123")
            >>> print(f"Policy: {request.triggered_policy_name}")
            >>> print(f"Severity: {request.severity}")
        """
        if self._config.debug:
            self._logger.debug("Getting HITL request", request_id=request_id)

        response = await self._request("GET", f"/api/v1/hitl/queue/{request_id}")
        data = response.get("data", response) if isinstance(response, dict) else response
        return HITLApprovalRequest.model_validate(data)

    async def approve_hitl_request(
        self,
        request_id: str,
        review: HITLReviewInput,
    ) -> None:
        """Approve a pending HITL approval request.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Args:
            request_id: ID of the approval request to approve
            review: Review input with reviewer details and optional comment

        Example:
            >>> await client.approve_hitl_request(
            ...     "hitl-req-123",
            ...     HITLReviewInput(
            ...         reviewer_id="user-456",
            ...         reviewer_email="admin@company.com",
            ...         comment="Reviewed and approved - query is safe",
            ...     ),
            ... )
        """
        if self._config.debug:
            self._logger.debug(
                "Approving HITL request",
                request_id=request_id,
                reviewer_id=review.reviewer_id,
            )

        await self._request(
            "POST",
            f"/api/v1/hitl/queue/{request_id}/approve",
            json_data=review.model_dump(exclude_none=True),
        )

    async def reject_hitl_request(
        self,
        request_id: str,
        review: HITLReviewInput,
    ) -> None:
        """Reject a pending HITL approval request.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Args:
            request_id: ID of the approval request to reject
            review: Review input with reviewer details and optional comment

        Example:
            >>> await client.reject_hitl_request(
            ...     "hitl-req-123",
            ...     HITLReviewInput(
            ...         reviewer_id="user-456",
            ...         reviewer_email="admin@company.com",
            ...         comment="Query contains sensitive data - rejected",
            ...     ),
            ... )
        """
        if self._config.debug:
            self._logger.debug(
                "Rejecting HITL request",
                request_id=request_id,
                reviewer_id=review.reviewer_id,
            )

        await self._request(
            "POST",
            f"/api/v1/hitl/queue/{request_id}/reject",
            json_data=review.model_dump(exclude_none=True),
        )

    async def get_hitl_stats(self) -> HITLStats:
        """Get HITL queue dashboard statistics.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Returns:
            HITLStats with queue statistics

        Example:
            >>> stats = await client.get_hitl_stats()
            >>> print(f"Pending: {stats.total_pending}")
            >>> print(f"Critical: {stats.critical_priority}")
        """
        if self._config.debug:
            self._logger.debug("Getting HITL stats")

        response = await self._request("GET", "/api/v1/hitl/stats")
        data = response.get("data", response) if isinstance(response, dict) else response
        return HITLStats.model_validate(data)

    # =========================================================================
    # Plan Rollback (Feature 7)
    # =========================================================================

    async def rollback_plan(
        self,
        plan_id: str,
        target_version: int,
    ) -> RollbackPlanResponse:
        """Rollback a plan to a previous version.

        Args:
            plan_id: ID of the plan to rollback
            target_version: Version number to rollback to

        Returns:
            RollbackPlanResponse with rollback confirmation

        Example:
            >>> result = await client.rollback_plan("plan-123", target_version=2)
            >>> print(f"Rolled back to version {result.version}")
        """
        response = await self._map_request(
            "POST",
            f"/api/v1/plan/{plan_id}/rollback/{target_version}",
        )
        return RollbackPlanResponse.model_validate(response)

    # =========================================================================
    # Webhook CRUD (Feature 7)
    # =========================================================================

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        secret: str = "",
        active: bool = True,
    ) -> WebhookSubscription:
        """Create a webhook subscription.

        Args:
            url: Webhook URL to receive events
            events: List of event types to subscribe to
            secret: Optional shared secret for webhook signature verification
            active: Whether the webhook is active (default: True)

        Returns:
            Created WebhookSubscription

        Example:
            >>> webhook = await client.create_webhook(
            ...     url="https://example.com/webhooks",
            ...     events=["workflow.completed", "step.approval_required"],
            ...     secret="my-secret",
            ... )
            >>> print(f"Webhook created: {webhook.id}")
        """
        body: dict[str, Any] = {
            "url": url,
            "events": events,
            "active": active,
        }
        if secret:
            body["secret"] = secret

        response = await self._request("POST", "/api/v1/webhooks", json_data=body)
        return WebhookSubscription.model_validate(response)

    async def get_webhook(self, webhook_id: str) -> WebhookSubscription:
        """Get a webhook subscription by ID.

        Args:
            webhook_id: Webhook subscription ID

        Returns:
            WebhookSubscription details

        Example:
            >>> webhook = await client.get_webhook("wh-123")
            >>> print(f"Webhook {webhook.id}: {webhook.url}")
        """
        response = await self._request("GET", f"/api/v1/webhooks/{webhook_id}")
        return WebhookSubscription.model_validate(response)

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        secret: str | None = None,
        active: bool | None = None,
        description: str | None = None,
    ) -> WebhookSubscription:
        """Update a webhook subscription.

        Args:
            webhook_id: Webhook subscription ID
            url: New webhook URL
            events: New list of event types to subscribe to
            secret: New shared secret for webhook signature verification
            active: Whether the webhook is active
            description: Webhook description

        Returns:
            Updated WebhookSubscription

        Example:
            >>> webhook = await client.update_webhook("wh-123", active=False)
            >>> print(f"Webhook active: {webhook.active}")
        """
        body: dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if events is not None:
            body["events"] = events
        if secret is not None:
            body["secret"] = secret
        if active is not None:
            body["active"] = active
        if description is not None:
            body["description"] = description

        response = await self._request("PUT", f"/api/v1/webhooks/{webhook_id}", json_data=body)
        return WebhookSubscription.model_validate(response)

    async def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook subscription.

        Args:
            webhook_id: Webhook subscription ID

        Example:
            >>> await client.delete_webhook("wh-123")
        """
        await self._request("DELETE", f"/api/v1/webhooks/{webhook_id}")

    async def list_webhooks(self) -> ListWebhooksResponse:
        """List all webhook subscriptions.

        Returns:
            ListWebhooksResponse with all webhook subscriptions

        Example:
            >>> result = await client.list_webhooks()
            >>> for wh in result.webhooks:
            ...     print(f"{wh.id}: {wh.url} ({len(wh.events)} events)")
        """
        response = await self._request("GET", "/api/v1/webhooks")

        webhooks = [WebhookSubscription.model_validate(w) for w in response.get("webhooks", [])]

        return ListWebhooksResponse(
            webhooks=webhooks,
            total=response.get("total", len(webhooks)),
        )

    # =========================================================================
    # MEDIA GOVERNANCE CONFIG
    # =========================================================================

    async def get_media_governance_config(self) -> MediaGovernanceConfig:
        """Get the media governance configuration for the current tenant.

        Returns:
            MediaGovernanceConfig with current tenant media governance settings

        Example:
            >>> config = await client.get_media_governance_config()
            >>> print(f"Enabled: {config.enabled}, Analyzers: {config.allowed_analyzers}")
        """
        response = await self._request("GET", "/api/v1/media-governance/config")
        return MediaGovernanceConfig.model_validate(response)

    async def update_media_governance_config(
        self,
        request: UpdateMediaGovernanceConfigRequest,
    ) -> MediaGovernanceConfig:
        """Update the media governance configuration for the current tenant.

        Args:
            request: Update request with fields to change

        Returns:
            Updated MediaGovernanceConfig

        Example:
            >>> from axonflow import UpdateMediaGovernanceConfigRequest
            >>> config = await client.update_media_governance_config(
            ...     UpdateMediaGovernanceConfigRequest(
            ...         enabled=True, allowed_analyzers=["nsfw", "pii"]
            ...     )
            ... )
            >>> print(f"Enabled: {config.enabled}")
        """
        response = await self._request(
            "PUT",
            "/api/v1/media-governance/config",
            json_data=request.model_dump(exclude_none=True),
        )
        return MediaGovernanceConfig.model_validate(response)

    async def get_media_governance_status(self) -> MediaGovernanceStatus:
        """Get the platform-level media governance status.

        Returns:
            MediaGovernanceStatus with availability and default configuration

        Example:
            >>> status = await client.get_media_governance_status()
            >>> print(f"Available: {status.available}, Tier: {status.tier}")
        """
        response = await self._request("GET", "/api/v1/media-governance/status")
        return MediaGovernanceStatus.model_validate(response)

    # =========================================================================
    # MAS FEAT COMPLIANCE (Enterprise)
    # =========================================================================

    async def masfeat_register_system(
        self,
        system_id: str,
        system_name: str,
        use_case: str,
        owner_team: str,
        customer_impact: int,
        model_complexity: int,
        human_reliance: int,
        *,
        description: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Register an AI system in the MAS FEAT registry.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Args:
            system_id: Unique system identifier
            system_name: Human-readable system name
            use_case: Primary use case (credit_scoring, robo_advisory, etc.)
            owner_team: Owning team name
            customer_impact: Customer impact rating (1-5)
            model_complexity: Model complexity rating (1-5)
            human_reliance: Human reliance rating (1-5)
            description: Optional system description
            technical_owner: Optional technical owner email
            business_owner: Optional business owner email
            metadata: Optional additional metadata

        Returns:
            Registered AI system with materiality classification

        Example:
            >>> system = await client.masfeat_register_system(
            ...     system_id="credit-scoring-v1",
            ...     system_name="Credit Scoring AI",
            ...     use_case="credit_scoring",
            ...     owner_team="Risk Management",
            ...     customer_impact=4,
            ...     model_complexity=3,
            ...     human_reliance=5,
            ... )
            >>> print(system.materiality)  # 'high' (sum=12)
        """
        body = {
            "system_id": system_id,
            "system_name": system_name,
            "use_case": use_case,
            "owner_team": owner_team,
            "risk_rating_impact": customer_impact,
            "risk_rating_complexity": model_complexity,
            "risk_rating_reliance": human_reliance,
        }
        if description is not None:
            body["description"] = description
        if technical_owner is not None:
            body["technical_owner"] = technical_owner
        if business_owner is not None:
            body["owner_email"] = business_owner
        if metadata is not None:
            body["metadata"] = metadata

        response = await self._request("POST", "/api/v1/masfeat/registry", json_data=body)
        return masfeat.ai_system_registry_from_dict(response)

    async def masfeat_get_system(self, system_id: str) -> AISystemRegistry:
        """Get an AI system from the registry.

        Args:
            system_id: System identifier

        Returns:
            AI system registry entry
        """
        response = await self._request("GET", f"/api/v1/masfeat/registry/{system_id}")
        return masfeat.ai_system_registry_from_dict(response)

    async def masfeat_update_system(
        self,
        system_id: str,
        *,
        system_name: str | None = None,
        description: str | None = None,
        owner_team: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        customer_impact: int | None = None,
        model_complexity: int | None = None,
        human_reliance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Update an AI system in the registry.

        Args:
            system_id: System identifier
            system_name: New system name
            description: New description
            owner_team: New owner team
            technical_owner: New technical owner
            business_owner: New business owner
            customer_impact: New customer impact rating
            model_complexity: New model complexity rating
            human_reliance: New human reliance rating
            metadata: New metadata

        Returns:
            Updated AI system
        """
        body: dict[str, Any] = {}
        if system_name is not None:
            body["system_name"] = system_name
        if description is not None:
            body["description"] = description
        if owner_team is not None:
            body["owner_team"] = owner_team
        if technical_owner is not None:
            body["technical_owner"] = technical_owner
        if business_owner is not None:
            body["business_owner"] = business_owner
        if customer_impact is not None:
            body["customer_impact"] = customer_impact
        if model_complexity is not None:
            body["model_complexity"] = model_complexity
        if human_reliance is not None:
            body["human_reliance"] = human_reliance
        if metadata is not None:
            body["metadata"] = metadata

        url = f"/api/v1/masfeat/registry/{system_id}"
        response = await self._request("PUT", url, json_data=body)
        return masfeat.ai_system_registry_from_dict(response)

    async def masfeat_list_systems(
        self,
        *,
        status: str | None = None,
        use_case: str | None = None,
        materiality: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AISystemRegistry]:
        """List AI systems in the registry.

        Args:
            status: Filter by status (draft, active, suspended, retired)
            use_case: Filter by use case
            materiality: Filter by materiality (high, medium, low)
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of AI systems
        """
        params: list[str] = []
        if status:
            params.append(f"status={status}")
        if use_case:
            params.append(f"use_case={use_case}")
        if materiality:
            params.append(f"materiality={materiality}")
        if limit:
            params.append(f"limit={limit}")
        if offset:
            params.append(f"offset={offset}")

        path = "/api/v1/masfeat/registry"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._request("GET", path)
        # Response is a list of system dicts for this endpoint
        systems: list[dict[str, Any]] = response or []  # type: ignore[assignment]
        return [masfeat.ai_system_registry_from_dict(s) for s in systems]

    async def masfeat_activate_system(self, system_id: str) -> AISystemRegistry:
        """Activate an AI system (transition from draft to active).

        Note: system_id should be the UUID (id field) returned from register_system,
        not the user-provided system_id field.

        Args:
            system_id: System UUID (the 'id' field from registration response)

        Returns:
            Activated AI system
        """
        response = await self._request(
            "PUT", f"/api/v1/masfeat/registry/{system_id}", json_data={"status": "active"}
        )
        return masfeat.ai_system_registry_from_dict(response)

    async def masfeat_retire_system(self, system_id: str) -> AISystemRegistry:
        """Retire an AI system.

        Args:
            system_id: System identifier

        Returns:
            Retired AI system
        """
        response = await self._request("DELETE", f"/api/v1/masfeat/registry/{system_id}")
        return masfeat.ai_system_registry_from_dict(response)

    async def masfeat_get_registry_summary(self) -> RegistrySummary:
        """Get a summary of the AI system registry.

        Returns:
            Registry summary with counts by materiality and status
        """
        response = await self._request("GET", "/api/v1/masfeat/registry/summary")
        return masfeat.registry_summary_from_dict(response)

    # -------------------------------------------------------------------------
    # FEAT Assessments
    # -------------------------------------------------------------------------

    async def masfeat_create_assessment(
        self,
        system_id: str,
        *,
        assessment_type: str = "periodic",
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Create a FEAT assessment for an AI system.

        Args:
            system_id: System identifier
            assessment_type: Assessment type (initial, periodic, ad_hoc)
            assessors: List of assessor emails

        Returns:
            Created assessment
        """
        body: dict[str, Any] = {
            "system_id": system_id,
            "assessment_type": assessment_type,
        }
        if assessors is not None:
            body["assessors"] = assessors

        response = await self._request("POST", "/api/v1/masfeat/assessments", json_data=body)
        return masfeat.feat_assessment_from_dict(response)

    async def masfeat_get_assessment(self, assessment_id: str) -> FEATAssessment:
        """Get a FEAT assessment.

        Args:
            assessment_id: Assessment identifier

        Returns:
            FEAT assessment
        """
        response = await self._request("GET", f"/api/v1/masfeat/assessments/{assessment_id}")
        return masfeat.feat_assessment_from_dict(response)

    async def masfeat_update_assessment(
        self,
        assessment_id: str,
        *,
        fairness_score: int | None = None,
        ethics_score: int | None = None,
        accountability_score: int | None = None,
        transparency_score: int | None = None,
        fairness_details: dict[str, Any] | None = None,
        ethics_details: dict[str, Any] | None = None,
        accountability_details: dict[str, Any] | None = None,
        transparency_details: dict[str, Any] | None = None,
        findings: list[Finding] | None = None,
        recommendations: list[str] | None = None,
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Update a FEAT assessment.

        Args:
            assessment_id: Assessment identifier
            fairness_score: Fairness pillar score (0-100)
            ethics_score: Ethics pillar score (0-100)
            accountability_score: Accountability pillar score (0-100)
            transparency_score: Transparency pillar score (0-100)
            fairness_details: Fairness assessment details
            ethics_details: Ethics assessment details
            accountability_details: Accountability assessment details
            transparency_details: Transparency assessment details
            findings: Assessment findings
            recommendations: Recommendations
            assessors: List of assessors

        Returns:
            Updated assessment
        """
        body: dict[str, Any] = {}
        if fairness_score is not None:
            body["fairness_score"] = fairness_score
        if ethics_score is not None:
            body["ethics_score"] = ethics_score
        if accountability_score is not None:
            body["accountability_score"] = accountability_score
        if transparency_score is not None:
            body["transparency_score"] = transparency_score
        if fairness_details is not None:
            body["fairness_details"] = fairness_details
        if ethics_details is not None:
            body["ethics_details"] = ethics_details
        if accountability_details is not None:
            body["accountability_details"] = accountability_details
        if transparency_details is not None:
            body["transparency_details"] = transparency_details
        if findings is not None:
            body["findings"] = [masfeat.finding_to_dict(f) for f in findings]
        if recommendations is not None:
            body["recommendations"] = recommendations
        if assessors is not None:
            body["assessors"] = assessors

        response = await self._request(
            "PUT", f"/api/v1/masfeat/assessments/{assessment_id}", json_data=body
        )
        return masfeat.feat_assessment_from_dict(response)

    async def masfeat_list_assessments(
        self,
        *,
        system_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[FEATAssessment]:
        """List FEAT assessments.

        Args:
            system_id: Filter by system ID
            status: Filter by status
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of assessments
        """
        params: list[str] = []
        if system_id:
            params.append(f"system_id={system_id}")
        if status:
            params.append(f"status={status}")
        if limit:
            params.append(f"limit={limit}")
        if offset:
            params.append(f"offset={offset}")

        path = "/api/v1/masfeat/assessments"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._request("GET", path)
        # Response is a list of assessment dicts for this endpoint
        assessments: list[dict[str, Any]] = response or []  # type: ignore[assignment]
        return [masfeat.feat_assessment_from_dict(a) for a in assessments]

    async def masfeat_submit_assessment(self, assessment_id: str) -> FEATAssessment:
        """Submit a FEAT assessment for approval.

        Args:
            assessment_id: Assessment identifier

        Returns:
            Submitted assessment
        """
        response = await self._request(
            "POST", f"/api/v1/masfeat/assessments/{assessment_id}/submit"
        )
        return masfeat.feat_assessment_from_dict(response)

    async def masfeat_approve_assessment(
        self,
        assessment_id: str,
        approved_by: str,
        *,
        comments: str | None = None,
    ) -> FEATAssessment:
        """Approve a FEAT assessment.

        Args:
            assessment_id: Assessment identifier
            approved_by: Approver email/name
            comments: Optional approval comments

        Returns:
            Approved assessment
        """
        body: dict[str, Any] = {"approved_by": approved_by}
        if comments is not None:
            body["comments"] = comments

        response = await self._request(
            "POST", f"/api/v1/masfeat/assessments/{assessment_id}/approve", json_data=body
        )
        return masfeat.feat_assessment_from_dict(response)

    async def masfeat_reject_assessment(
        self,
        assessment_id: str,
        rejected_by: str,
        reason: str,
    ) -> FEATAssessment:
        """Reject a FEAT assessment.

        Args:
            assessment_id: Assessment identifier
            rejected_by: Rejector email/name
            reason: Rejection reason

        Returns:
            Rejected assessment
        """
        body = {"rejected_by": rejected_by, "reason": reason}
        response = await self._request(
            "POST", f"/api/v1/masfeat/assessments/{assessment_id}/reject", json_data=body
        )
        return masfeat.feat_assessment_from_dict(response)

    # -------------------------------------------------------------------------
    # Kill Switch
    # -------------------------------------------------------------------------

    async def masfeat_get_kill_switch(self, system_id: str) -> KillSwitch:
        """Get kill switch configuration for an AI system.

        Args:
            system_id: System identifier

        Returns:
            Kill switch configuration and status
        """
        response = await self._request("GET", f"/api/v1/masfeat/killswitch/{system_id}")
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_configure_kill_switch(
        self,
        system_id: str,
        *,
        accuracy_threshold: float | None = None,
        bias_threshold: float | None = None,
        error_rate_threshold: float | None = None,
        auto_trigger_enabled: bool | None = None,
    ) -> KillSwitch:
        """Configure kill switch thresholds for an AI system.

        Args:
            system_id: System identifier
            accuracy_threshold: Minimum accuracy threshold (0-1)
            bias_threshold: Maximum bias threshold (0-1)
            error_rate_threshold: Maximum error rate threshold (0-1)
            auto_trigger_enabled: Enable automatic triggering

        Returns:
            Configured kill switch
        """
        body: dict[str, Any] = {}
        if accuracy_threshold is not None:
            body["accuracy_threshold"] = accuracy_threshold
        if bias_threshold is not None:
            body["bias_threshold"] = bias_threshold
        if error_rate_threshold is not None:
            body["error_rate_threshold"] = error_rate_threshold
        if auto_trigger_enabled is not None:
            body["auto_trigger_enabled"] = auto_trigger_enabled

        response = await self._request(
            "POST", f"/api/v1/masfeat/killswitch/{system_id}/configure", json_data=body
        )
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_check_kill_switch(
        self,
        system_id: str,
        accuracy: float,
        *,
        bias_score: float | None = None,
        error_rate: float | None = None,
    ) -> KillSwitch:
        """Check current metrics against kill switch thresholds.

        If auto-trigger is enabled and thresholds are breached,
        the kill switch will be automatically triggered.

        Args:
            system_id: System identifier
            accuracy: Current model accuracy (0-1)
            bias_score: Current bias score (0-1)
            error_rate: Current error rate (0-1)

        Returns:
            Kill switch status (may be triggered)
        """
        body: dict[str, Any] = {"accuracy": accuracy}
        if bias_score is not None:
            body["bias_score"] = bias_score
        if error_rate is not None:
            body["error_rate"] = error_rate

        response = await self._request(
            "POST", f"/api/v1/masfeat/killswitch/{system_id}/check", json_data=body
        )
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_trigger_kill_switch(
        self,
        system_id: str,
        reason: str,
        *,
        triggered_by: str | None = None,
    ) -> KillSwitch:
        """Manually trigger the kill switch for an AI system.

        Args:
            system_id: System identifier
            reason: Reason for triggering
            triggered_by: Person who triggered (email/name)

        Returns:
            Triggered kill switch
        """
        body: dict[str, Any] = {"reason": reason}
        if triggered_by is not None:
            body["triggered_by"] = triggered_by

        response = await self._request(
            "POST", f"/api/v1/masfeat/killswitch/{system_id}/trigger", json_data=body
        )
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_restore_kill_switch(
        self,
        system_id: str,
        reason: str,
        *,
        restored_by: str | None = None,
    ) -> KillSwitch:
        """Restore (un-trigger) the kill switch for an AI system.

        Args:
            system_id: System identifier
            reason: Reason for restoration
            restored_by: Person who restored (email/name)

        Returns:
            Restored kill switch
        """
        body: dict[str, Any] = {"reason": reason}
        if restored_by is not None:
            body["restored_by"] = restored_by

        response = await self._request(
            "POST", f"/api/v1/masfeat/killswitch/{system_id}/restore", json_data=body
        )
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_enable_kill_switch(self, system_id: str) -> KillSwitch:
        """Enable the kill switch for an AI system.

        Args:
            system_id: System identifier

        Returns:
            Enabled kill switch
        """
        response = await self._request("POST", f"/api/v1/masfeat/killswitch/{system_id}/enable")
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_disable_kill_switch(
        self,
        system_id: str,
        *,
        reason: str | None = None,
    ) -> KillSwitch:
        """Disable the kill switch for an AI system.

        Args:
            system_id: System identifier
            reason: Optional reason for disabling

        Returns:
            Disabled kill switch
        """
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason

        response = await self._request(
            "POST", f"/api/v1/masfeat/killswitch/{system_id}/disable", json_data=body
        )
        return masfeat.kill_switch_from_dict(response)

    async def masfeat_get_kill_switch_history(
        self,
        system_id: str,
        *,
        limit: int | None = None,
    ) -> list[KillSwitchEvent]:
        """Get kill switch event history.

        Args:
            system_id: System identifier
            limit: Maximum number of events

        Returns:
            List of kill switch events
        """
        params: list[str] = []
        if limit:
            params.append(f"limit={limit}")

        path = f"/api/v1/masfeat/killswitch/{system_id}/history"
        if params:
            path = f"{path}?{'&'.join(params)}"

        response = await self._request("GET", path)
        # Handle nested response format {history: [], count: 0}
        events_data: list[dict[str, Any]]
        if isinstance(response, dict) and "history" in response:
            events_data = response["history"]
        else:
            events_data = response or []  # type: ignore[assignment]
        return [masfeat.kill_switch_event_from_dict(e) for e in events_data]

    # ============================================================================
    # Unified Execution Tracking Methods (Issue #1075)
    # ============================================================================

    async def get_execution_status(self, execution_id: str) -> ExecutionStatus:
        """Get unified execution status for a MAP plan or WCP workflow.

        This method provides a consistent interface for tracking execution progress
        regardless of whether the underlying execution is a MAP plan or WCP workflow.

        Args:
            execution_id: The execution ID (plan ID or workflow ID)

        Returns:
            Unified execution status

        Example:
            >>> # Get status for any execution (MAP or WCP)
            >>> status = await client.get_execution_status('exec_123')
            >>> print(f"Type: {status.execution_type}")
            >>> print(f"Status: {status.status}")
            >>> print(f"Progress: {status.progress_percent}%")
            >>>
            >>> # Check steps
            >>> for step in status.steps:
            ...     print(f"  Step {step.step_index}: {step.step_name} - {step.status}")
        """
        if not execution_id:
            msg = "Execution ID is required"
            raise ValueError(msg)

        if self._config.debug:
            self._logger.debug("Getting execution status", execution_id=execution_id)

        path = f"/api/v1/unified/executions/{execution_id}"
        response = await self._orchestrator_request("GET", path)
        if not isinstance(response, dict):
            msg = "Unexpected response type from get execution status"
            raise TypeError(msg)

        return self._map_execution_status(response)

    async def list_unified_executions(
        self,
        options: UnifiedListExecutionsRequest | None = None,
    ) -> UnifiedListExecutionsResponse:
        """List unified executions with optional filters.

        Returns a paginated list of executions (both MAP plans and WCP workflows)
        with optional filtering by type, status, tenant, or organization.
        This method provides a unified view across all execution types.

        Args:
            options: Filter and pagination options

        Returns:
            Paginated list of unified executions

        Example:
            >>> # List all running executions
            >>> result = await client.list_unified_executions(
            ...     UnifiedListExecutionsRequest(
            ...         status=ExecutionStatusValue.RUNNING,
            ...         limit=20
            ...     )
            ... )
            >>> print(f"Found {result.total} running executions")
            >>>
            >>> # List only MAP plans
            >>> map_plans = await client.list_unified_executions(
            ...     UnifiedListExecutionsRequest(
            ...         execution_type=ExecutionType.MAP_PLAN,
            ...         limit=50
            ...     )
            ... )
        """
        params: list[str] = []
        if options:
            if options.execution_type:
                params.append(f"execution_type={options.execution_type.value}")
            if options.status:
                params.append(f"status={options.status.value}")
            if options.tenant_id:
                params.append(f"tenant_id={options.tenant_id}")
            if options.org_id:
                params.append(f"org_id={options.org_id}")
            if options.limit:
                params.append(f"limit={options.limit}")
            if options.offset:
                params.append(f"offset={options.offset}")

        path = "/api/v1/unified/executions"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing unified executions", options=options)

        response = await self._orchestrator_request("GET", path)
        if not isinstance(response, dict):
            msg = "Unexpected response type from list executions"
            raise TypeError(msg)

        raw_executions = response.get("executions") or []
        executions = [self._map_execution_status(e) for e in raw_executions]

        return UnifiedListExecutionsResponse(
            executions=executions,
            total=response.get("total", len(executions)),
            limit=response.get("limit", 50),
            offset=response.get("offset", 0),
            has_more=response.get("has_more", False),
        )

    async def cancel_execution(
        self,
        execution_id: str,
        reason: str | None = None,
    ) -> None:
        """Cancel a unified execution (MAP plan or WCP workflow).

        This method cancels an execution via the unified execution API,
        automatically propagating to the correct subsystem (MAP or WCP).

        Args:
            execution_id: The execution ID (plan ID or workflow ID)
            reason: Optional reason for cancellation

        Example:
            >>> await client.cancel_execution("wf_abc123", "User requested cancellation")
        """
        if not execution_id:
            msg = "Execution ID is required"
            raise ValueError(msg)

        body = {"reason": reason} if reason else {}

        await self._orchestrator_request(
            "POST",
            f"/api/v1/unified/executions/{execution_id}/cancel",
            json_data=body,
        )

        if self._config.debug:
            self._logger.debug("Cancelled execution", execution_id=execution_id, reason=reason)

    async def stream_execution_status(
        self,
        execution_id: str,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[ExecutionStatus]:
        """Stream real-time execution status updates via Server-Sent Events (SSE).

        Connects to GET /api/v1/unified/executions/{execution_id}/stream and yields
        ExecutionStatus objects as they arrive. The stream ends when a terminal
        status is received (completed, failed, cancelled, aborted, expired)
        or when the connection is closed.

        Args:
            execution_id: The execution ID to stream updates for
            timeout: Optional overall timeout in seconds for the stream connection.
                     Defaults to None (no timeout, stream runs until terminal status).

        Yields:
            ExecutionStatus objects with real-time progress updates

        Raises:
            ValueError: If execution_id is empty
            ConnectionError: If unable to connect to the SSE endpoint
            TimeoutError: If the stream times out

        Example:
            >>> async for status in client.stream_execution_status('exec_123'):
            ...     print(f"Status: {status.status}, Progress: {status.progress_percent}%")
            ...     if status.is_terminal():
            ...         print(f"Execution finished: {status.status}")
        """
        if not execution_id:
            msg = "Execution ID is required"
            raise ValueError(msg)

        url = f"{self._config.endpoint}/api/v1/unified/executions/{execution_id}/stream"

        # Build headers for SSE
        headers = dict(self._http_client.headers)
        headers["Accept"] = "text/event-stream"
        headers["Cache-Control"] = "no-cache"

        stream_timeout = httpx.Timeout(
            timeout if timeout is not None else None,
            connect=30.0,
        )

        if self._config.debug:
            self._logger.debug(
                "Connecting to execution stream",
                execution_id=execution_id,
                url=url,
            )

        try:
            async with (
                httpx.AsyncClient(
                    timeout=stream_timeout,
                    verify=not self._config.insecure_skip_verify,
                    headers=headers,
                ) as stream_client,
                stream_client.stream("GET", url) as response,
            ):
                if response.status_code != 200:  # noqa: PLR2004
                    await response.aread()
                    msg = (
                        f"SSE stream connection failed: "
                        f"HTTP {response.status_code} for execution {execution_id}"
                    )
                    raise AxonFlowError(msg)

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk

                    # Process complete SSE events (delimited by double newline)
                    while "\n\n" in buffer:
                        event_text, buffer = buffer.split("\n\n", 1)

                        for line in event_text.split("\n"):
                            stripped = line.strip()

                            data = self._parse_sse_line(stripped)
                            if data is None:
                                continue

                            status = self._map_execution_status(data)

                            if self._config.debug:
                                self._logger.debug(
                                    "SSE: execution status update",
                                    execution_id=execution_id,
                                    status=status.status.value,
                                    progress=status.progress_percent,
                                )

                            yield status

                            # Stop if terminal status reached
                            if status.is_terminal():
                                return

        except httpx.ConnectError as e:
            msg = f"Failed to connect to execution stream: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Execution stream timed out: {e}"
            raise TimeoutError(msg) from e

    def _parse_sse_line(self, line: str) -> dict[str, Any] | None:
        """Parse a single SSE line and return the decoded JSON data, or None."""
        # Skip empty lines and SSE comments
        if not line or line.startswith(":"):
            return None

        # Parse "data: {json}" lines
        if line.startswith("data: "):
            data_str = line[6:]
        elif line.startswith("data:"):
            data_str = line[5:]
        else:
            return None

        data_str = data_str.strip()
        if not data_str:
            return None

        try:
            parsed: dict[str, Any] = json.loads(data_str)
        except json.JSONDecodeError:
            if self._config.debug:
                self._logger.warning(
                    "SSE: failed to parse status event",
                    data=data_str[:200],
                )
            return None
        else:
            return parsed

    def _map_execution_status(self, data: dict[str, Any]) -> ExecutionStatus:
        """Map API response to ExecutionStatus."""
        steps = []
        if data.get("steps"):
            for s in data["steps"]:
                steps.append(
                    UnifiedStepStatus(
                        step_id=s["step_id"],
                        step_index=s["step_index"],
                        step_name=s.get("step_name", ""),
                        step_type=UnifiedStepType(s["step_type"]),
                        status=StepStatusValue(s["status"]),
                        started_at=(
                            _parse_datetime(s["started_at"]) if s.get("started_at") else None
                        ),
                        ended_at=_parse_datetime(s["ended_at"]) if s.get("ended_at") else None,
                        duration=s.get("duration"),
                        decision=UnifiedGateDecision(s["decision"]) if s.get("decision") else None,
                        decision_reason=s.get("decision_reason"),
                        policies_matched=s.get("policies_matched", []),
                        approval_status=UnifiedApprovalStatus(s["approval_status"])
                        if s.get("approval_status")
                        else None,
                        approved_by=s.get("approved_by"),
                        approved_at=_parse_datetime(s["approved_at"])
                        if s.get("approved_at")
                        else None,
                        model=s.get("model"),
                        provider=s.get("provider"),
                        cost_usd=s.get("cost_usd"),
                        input=s.get("input"),
                        output=s.get("output"),
                        result_summary=s.get("result_summary"),
                        error=s.get("error"),
                    )
                )

        return ExecutionStatus(
            execution_id=data["execution_id"],
            execution_type=ExecutionType(data["execution_type"]),
            name=data["name"],
            source=data.get("source"),
            status=ExecutionStatusValue(data["status"]),
            current_step_index=data.get("current_step_index", 0),
            total_steps=data.get("total_steps", 0),
            progress_percent=data.get("progress_percent", 0.0),
            started_at=_parse_datetime(data["started_at"]),
            completed_at=(
                _parse_datetime(data["completed_at"]) if data.get("completed_at") else None
            ),
            duration=data.get("duration"),
            estimated_cost_usd=data.get("estimated_cost_usd"),
            actual_cost_usd=data.get("actual_cost_usd"),
            steps=steps,
            error=data.get("error"),
            tenant_id=data.get("tenant_id"),
            org_id=data.get("org_id"),
            user_id=data.get("user_id"),
            client_id=data.get("client_id"),
            metadata=data.get("metadata", {}),
            created_at=_parse_datetime(data["created_at"]),
            updated_at=_parse_datetime(data["updated_at"]),
        )

    def _map_workflow_response(self, data: dict[str, Any]) -> WorkflowStatusResponse:
        """Map API response to WorkflowStatusResponse."""
        steps = []
        if data.get("steps"):
            for s in data["steps"]:
                steps.append(
                    WorkflowStepInfo(
                        step_id=s["step_id"],
                        step_index=s["step_index"],
                        step_name=s.get("step_name"),
                        step_type=StepType(s["step_type"]),
                        decision=GateDecision(s["decision"]),
                        decision_reason=s.get("decision_reason"),
                        approval_status=ApprovalStatus(s["approval_status"])
                        if s.get("approval_status")
                        else None,
                        approved_by=s.get("approved_by"),
                        gate_checked_at=_parse_datetime(s["gate_checked_at"]),
                        completed_at=_parse_datetime(s["completed_at"])
                        if s.get("completed_at")
                        else None,
                    )
                )

        return WorkflowStatusResponse(
            workflow_id=data["workflow_id"],
            workflow_name=data["workflow_name"],
            source=WorkflowSource(data["source"]),
            status=WorkflowStatus(data["status"]),
            current_step_index=data.get("current_step_index", 0),
            total_steps=data.get("total_steps"),
            started_at=_parse_datetime(data["started_at"]),
            completed_at=(
                _parse_datetime(data["completed_at"]) if data.get("completed_at") else None
            ),
            trace_id=data.get("trace_id"),
            metadata=data.get("metadata"),
            steps=steps,
        )


class MASFEATNamespace:
    """MAS FEAT compliance methods namespace for async client.

    Provides a namespace for MAS FEAT compliance methods on the AxonFlow client.
    Access via `client.masfeat.register_system()` etc.

    Enterprise Feature: Requires AxonFlow Enterprise license.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AxonFlow) -> None:
        self._client = client

    # Registry methods
    async def register_system(
        self,
        system_id: str,
        system_name: str,
        use_case: str,
        owner_team: str,
        customer_impact: int,
        model_complexity: int,
        human_reliance: int,
        *,
        description: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Register an AI system in the MAS FEAT registry."""
        return await self._client.masfeat_register_system(
            system_id=system_id,
            system_name=system_name,
            use_case=use_case,
            owner_team=owner_team,
            customer_impact=customer_impact,
            model_complexity=model_complexity,
            human_reliance=human_reliance,
            description=description,
            technical_owner=technical_owner,
            business_owner=business_owner,
            metadata=metadata,
        )

    async def get_system(self, system_id: str) -> AISystemRegistry:
        """Get an AI system from the registry."""
        return await self._client.masfeat_get_system(system_id)

    async def update_system(
        self,
        system_id: str,
        *,
        system_name: str | None = None,
        description: str | None = None,
        owner_team: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        customer_impact: int | None = None,
        model_complexity: int | None = None,
        human_reliance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Update an AI system in the registry."""
        return await self._client.masfeat_update_system(
            system_id,
            system_name=system_name,
            description=description,
            owner_team=owner_team,
            technical_owner=technical_owner,
            business_owner=business_owner,
            customer_impact=customer_impact,
            model_complexity=model_complexity,
            human_reliance=human_reliance,
            metadata=metadata,
        )

    async def list_systems(
        self,
        *,
        use_case: str | None = None,
        status: str | None = None,
        materiality: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AISystemRegistry]:
        """List AI systems in the registry."""
        return await self._client.masfeat_list_systems(
            use_case=use_case,
            status=status,
            materiality=materiality,
            limit=limit,
            offset=offset,
        )

    async def activate_system(self, system_id: str) -> AISystemRegistry:
        """Activate an AI system."""
        return await self._client.masfeat_activate_system(system_id)

    async def retire_system(self, system_id: str) -> AISystemRegistry:
        """Retire an AI system."""
        return await self._client.masfeat_retire_system(system_id)

    async def get_registry_summary(self) -> RegistrySummary:
        """Get registry summary statistics."""
        return await self._client.masfeat_get_registry_summary()

    # Assessment methods
    async def create_assessment(
        self,
        system_id: str,
        assessment_type: str,
        *,
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Create a new FEAT assessment."""
        return await self._client.masfeat_create_assessment(
            system_id=system_id,
            assessment_type=assessment_type,
            assessors=assessors,
        )

    async def get_assessment(self, assessment_id: str) -> FEATAssessment:
        """Get a FEAT assessment."""
        return await self._client.masfeat_get_assessment(assessment_id)

    async def update_assessment(
        self,
        assessment_id: str,
        *,
        fairness_score: int | None = None,
        ethics_score: int | None = None,
        accountability_score: int | None = None,
        transparency_score: int | None = None,
        fairness_details: dict[str, Any] | None = None,
        ethics_details: dict[str, Any] | None = None,
        accountability_details: dict[str, Any] | None = None,
        transparency_details: dict[str, Any] | None = None,
        findings: list[Finding] | None = None,
        recommendations: list[str] | None = None,
    ) -> FEATAssessment:
        """Update a FEAT assessment with scores and details."""
        return await self._client.masfeat_update_assessment(
            assessment_id=assessment_id,
            fairness_score=fairness_score,
            ethics_score=ethics_score,
            accountability_score=accountability_score,
            transparency_score=transparency_score,
            fairness_details=fairness_details,
            ethics_details=ethics_details,
            accountability_details=accountability_details,
            transparency_details=transparency_details,
            findings=findings,
            recommendations=recommendations,
        )

    async def list_assessments(
        self,
        *,
        system_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[FEATAssessment]:
        """List FEAT assessments."""
        return await self._client.masfeat_list_assessments(
            system_id=system_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def submit_assessment(self, assessment_id: str) -> FEATAssessment:
        """Submit an assessment for review."""
        return await self._client.masfeat_submit_assessment(assessment_id)

    async def approve_assessment(
        self,
        assessment_id: str,
        *,
        approved_by: str,
        comments: str | None = None,
    ) -> FEATAssessment:
        """Approve a FEAT assessment."""
        return await self._client.masfeat_approve_assessment(
            assessment_id=assessment_id,
            approved_by=approved_by,
            comments=comments,
        )

    async def reject_assessment(
        self,
        assessment_id: str,
        *,
        rejected_by: str,
        reason: str,
    ) -> FEATAssessment:
        """Reject a FEAT assessment."""
        return await self._client.masfeat_reject_assessment(
            assessment_id=assessment_id,
            rejected_by=rejected_by,
            reason=reason,
        )

    # Kill switch methods
    async def get_kill_switch(self, system_id: str) -> KillSwitch:
        """Get kill switch status."""
        return await self._client.masfeat_get_kill_switch(system_id)

    async def configure_kill_switch(
        self,
        system_id: str,
        *,
        accuracy_threshold: float | None = None,
        bias_threshold: float | None = None,
        error_rate_threshold: float | None = None,
        auto_trigger_enabled: bool | None = None,
    ) -> KillSwitch:
        """Configure kill switch thresholds."""
        return await self._client.masfeat_configure_kill_switch(
            system_id=system_id,
            accuracy_threshold=accuracy_threshold,
            bias_threshold=bias_threshold,
            error_rate_threshold=error_rate_threshold,
            auto_trigger_enabled=auto_trigger_enabled,
        )

    async def check_kill_switch(
        self,
        system_id: str,
        accuracy: float,
        *,
        bias_score: float | None = None,
        error_rate: float | None = None,
    ) -> KillSwitch:
        """Check metrics against kill switch thresholds."""
        return await self._client.masfeat_check_kill_switch(
            system_id=system_id,
            accuracy=accuracy,
            bias_score=bias_score,
            error_rate=error_rate,
        )

    async def trigger_kill_switch(
        self,
        system_id: str,
        *,
        reason: str,
        triggered_by: str,
    ) -> KillSwitch:
        """Manually trigger the kill switch."""
        return await self._client.masfeat_trigger_kill_switch(
            system_id=system_id,
            reason=reason,
            triggered_by=triggered_by,
        )

    async def restore_kill_switch(
        self,
        system_id: str,
        *,
        reason: str,
        restored_by: str,
    ) -> KillSwitch:
        """Restore the kill switch after a trigger."""
        return await self._client.masfeat_restore_kill_switch(
            system_id=system_id,
            reason=reason,
            restored_by=restored_by,
        )

    async def enable_kill_switch(self, system_id: str) -> KillSwitch:
        """Enable the kill switch."""
        return await self._client.masfeat_enable_kill_switch(system_id)

    async def disable_kill_switch(
        self,
        system_id: str,
        *,
        reason: str | None = None,
    ) -> KillSwitch:
        """Disable the kill switch."""
        return await self._client.masfeat_disable_kill_switch(
            system_id=system_id,
            reason=reason,
        )

    async def get_kill_switch_history(
        self,
        system_id: str,
        *,
        limit: int | None = None,
    ) -> list[KillSwitchEvent]:
        """Get kill switch event history."""
        return await self._client.masfeat_get_kill_switch_history(
            system_id=system_id,
            limit=limit,
        )


class SyncMASFEATNamespace:
    """MAS FEAT compliance methods namespace for sync client.

    Provides a namespace for MAS FEAT compliance methods on the SyncAxonFlow client.
    Access via `client.masfeat.register_system()` etc.

    Enterprise Feature: Requires AxonFlow Enterprise license.
    """

    __slots__ = ("_client",)

    def __init__(self, client: SyncAxonFlow) -> None:
        self._client = client

    # Registry methods
    def register_system(
        self,
        system_id: str,
        system_name: str,
        use_case: str,
        owner_team: str,
        customer_impact: int,
        model_complexity: int,
        human_reliance: int,
        *,
        description: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Register an AI system in the MAS FEAT registry."""
        return self._client.masfeat_register_system(
            system_id=system_id,
            system_name=system_name,
            use_case=use_case,
            owner_team=owner_team,
            customer_impact=customer_impact,
            model_complexity=model_complexity,
            human_reliance=human_reliance,
            description=description,
            technical_owner=technical_owner,
            business_owner=business_owner,
            metadata=metadata,
        )

    def get_system(self, system_id: str) -> AISystemRegistry:
        """Get an AI system from the registry."""
        return self._client.masfeat_get_system(system_id)

    def update_system(
        self,
        system_id: str,
        *,
        system_name: str | None = None,
        description: str | None = None,
        owner_team: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        customer_impact: int | None = None,
        model_complexity: int | None = None,
        human_reliance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Update an AI system in the registry."""
        return self._client.masfeat_update_system(
            system_id,
            system_name=system_name,
            description=description,
            owner_team=owner_team,
            technical_owner=technical_owner,
            business_owner=business_owner,
            customer_impact=customer_impact,
            model_complexity=model_complexity,
            human_reliance=human_reliance,
            metadata=metadata,
        )

    def list_systems(
        self,
        *,
        use_case: str | None = None,
        status: str | None = None,
        materiality: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AISystemRegistry]:
        """List AI systems in the registry."""
        return self._client.masfeat_list_systems(
            use_case=use_case,
            status=status,
            materiality=materiality,
            limit=limit,
            offset=offset,
        )

    def activate_system(self, system_id: str) -> AISystemRegistry:
        """Activate an AI system."""
        return self._client.masfeat_activate_system(system_id)

    def retire_system(self, system_id: str) -> AISystemRegistry:
        """Retire an AI system."""
        return self._client.masfeat_retire_system(system_id)

    def get_registry_summary(self) -> RegistrySummary:
        """Get registry summary statistics."""
        return self._client.masfeat_get_registry_summary()

    # Assessment methods
    def create_assessment(
        self,
        system_id: str,
        assessment_type: str,
        *,
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Create a new FEAT assessment."""
        return self._client.masfeat_create_assessment(
            system_id=system_id,
            assessment_type=assessment_type,
            assessors=assessors,
        )

    def get_assessment(self, assessment_id: str) -> FEATAssessment:
        """Get a FEAT assessment."""
        return self._client.masfeat_get_assessment(assessment_id)

    def update_assessment(
        self,
        assessment_id: str,
        *,
        fairness_score: int | None = None,
        ethics_score: int | None = None,
        accountability_score: int | None = None,
        transparency_score: int | None = None,
        fairness_details: dict[str, Any] | None = None,
        ethics_details: dict[str, Any] | None = None,
        accountability_details: dict[str, Any] | None = None,
        transparency_details: dict[str, Any] | None = None,
        findings: list[Finding] | None = None,
        recommendations: list[str] | None = None,
    ) -> FEATAssessment:
        """Update a FEAT assessment with scores and details."""
        return self._client.masfeat_update_assessment(
            assessment_id=assessment_id,
            fairness_score=fairness_score,
            ethics_score=ethics_score,
            accountability_score=accountability_score,
            transparency_score=transparency_score,
            fairness_details=fairness_details,
            ethics_details=ethics_details,
            accountability_details=accountability_details,
            transparency_details=transparency_details,
            findings=findings,
            recommendations=recommendations,
        )

    def list_assessments(
        self,
        *,
        system_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[FEATAssessment]:
        """List FEAT assessments."""
        return self._client.masfeat_list_assessments(
            system_id=system_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def submit_assessment(self, assessment_id: str) -> FEATAssessment:
        """Submit an assessment for review."""
        return self._client.masfeat_submit_assessment(assessment_id)

    def approve_assessment(
        self,
        assessment_id: str,
        *,
        approved_by: str,
        comments: str | None = None,
    ) -> FEATAssessment:
        """Approve a FEAT assessment."""
        return self._client.masfeat_approve_assessment(
            assessment_id=assessment_id,
            approved_by=approved_by,
            comments=comments,
        )

    def reject_assessment(
        self,
        assessment_id: str,
        *,
        rejected_by: str,
        reason: str,
    ) -> FEATAssessment:
        """Reject a FEAT assessment."""
        return self._client.masfeat_reject_assessment(
            assessment_id=assessment_id,
            rejected_by=rejected_by,
            reason=reason,
        )

    # Kill switch methods
    def get_kill_switch(self, system_id: str) -> KillSwitch:
        """Get kill switch status."""
        return self._client.masfeat_get_kill_switch(system_id)

    def configure_kill_switch(
        self,
        system_id: str,
        *,
        accuracy_threshold: float | None = None,
        bias_threshold: float | None = None,
        error_rate_threshold: float | None = None,
        auto_trigger_enabled: bool | None = None,
    ) -> KillSwitch:
        """Configure kill switch thresholds."""
        return self._client.masfeat_configure_kill_switch(
            system_id=system_id,
            accuracy_threshold=accuracy_threshold,
            bias_threshold=bias_threshold,
            error_rate_threshold=error_rate_threshold,
            auto_trigger_enabled=auto_trigger_enabled,
        )

    def check_kill_switch(
        self,
        system_id: str,
        accuracy: float,
        *,
        bias_score: float | None = None,
        error_rate: float | None = None,
    ) -> KillSwitch:
        """Check metrics against kill switch thresholds."""
        return self._client.masfeat_check_kill_switch(
            system_id=system_id,
            accuracy=accuracy,
            bias_score=bias_score,
            error_rate=error_rate,
        )

    def trigger_kill_switch(
        self,
        system_id: str,
        *,
        reason: str,
        triggered_by: str,
    ) -> KillSwitch:
        """Manually trigger the kill switch."""
        return self._client.masfeat_trigger_kill_switch(
            system_id=system_id,
            reason=reason,
            triggered_by=triggered_by,
        )

    def restore_kill_switch(
        self,
        system_id: str,
        *,
        reason: str,
        restored_by: str,
    ) -> KillSwitch:
        """Restore the kill switch after a trigger."""
        return self._client.masfeat_restore_kill_switch(
            system_id=system_id,
            reason=reason,
            restored_by=restored_by,
        )

    def enable_kill_switch(self, system_id: str) -> KillSwitch:
        """Enable the kill switch."""
        return self._client.masfeat_enable_kill_switch(system_id)

    def disable_kill_switch(
        self,
        system_id: str,
        *,
        reason: str | None = None,
    ) -> KillSwitch:
        """Disable the kill switch."""
        return self._client.masfeat_disable_kill_switch(
            system_id=system_id,
            reason=reason,
        )

    def get_kill_switch_history(
        self,
        system_id: str,
        *,
        limit: int | None = None,
    ) -> list[KillSwitchEvent]:
        """Get kill switch event history."""
        return self._client.masfeat_get_kill_switch_history(
            system_id=system_id,
            limit=limit,
        )


class SyncAxonFlow:
    """Synchronous wrapper for AxonFlow client.

    Wraps all async methods for synchronous usage.
    """

    __slots__ = ("_async_client", "_loop", "_owns_loop", "_masfeat")

    def __init__(self, async_client: AxonFlow) -> None:
        self._async_client = async_client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._owns_loop: bool = False
        self._masfeat: SyncMASFEATNamespace | None = None

    @property
    def masfeat(self) -> SyncMASFEATNamespace:
        """MAS FEAT compliance methods namespace.

        Enterprise Feature: Requires AxonFlow Enterprise license.

        Example:
            >>> client = AxonFlow.sync(endpoint="...")
            >>> system = client.masfeat.register_system(
            ...     system_id="credit-scoring-v1",
            ...     system_name="Credit Scoring AI",
            ...     use_case="credit_scoring",
            ...     owner_team="Risk Management",
            ...     customer_impact=4,
            ...     model_complexity=3,
            ...     human_reliance=5,
            ... )
            >>> print(system.materiality)  # 'high'
        """
        if self._masfeat is None:
            self._masfeat = SyncMASFEATNamespace(self)
        return self._masfeat

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop for synchronous execution."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
                if self._loop.is_running():
                    # Loop exists but is running, create our own
                    self._loop = asyncio.new_event_loop()
                    self._owns_loop = True
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                self._owns_loop = True
        return self._loop

    def _run_sync(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine synchronously, handling nested event loops."""
        # Check if there's a running loop in the current thread
        try:
            asyncio.get_running_loop()
            # We're inside an async context - run in a thread pool
            # This avoids "This event loop is already running" errors
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop - safe to use run_until_complete
            return self._get_loop().run_until_complete(coro)

    def __enter__(self) -> SyncAxonFlow:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the client and clean up resources."""
        self._run_sync(self._async_client.close())
        # Close the event loop if we created it
        if self._owns_loop and self._loop is not None and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    @property
    def config(self) -> AxonFlowConfig:
        """Get client configuration."""
        return self._async_client.config

    def health_check(self) -> bool:
        """Check if AxonFlow Agent is healthy."""
        return self._run_sync(self._async_client.health_check())

    def health_check_detailed(self) -> HealthResponse:
        """Get detailed health info including capabilities and version."""
        return self._run_sync(self._async_client.health_check_detailed())

    def orchestrator_health_check(self) -> bool:
        """Check if AxonFlow Orchestrator is healthy."""
        return self._run_sync(self._async_client.orchestrator_health_check())

    def proxy_llm_call(
        self,
        user_token: str,
        query: str,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Send a query through AxonFlow with full policy enforcement (Proxy Mode).

        This is Proxy Mode - AxonFlow acts as an intermediary, making the LLM call
        on your behalf.

        If user_token is empty, defaults to "anonymous" for audit purposes.
        """
        return self._run_sync(
            self._async_client.proxy_llm_call(user_token, query, request_type, context)
        )

    def proxy_llm_call_with_media(
        self,
        user_token: str,
        query: str,
        request_type: str,
        media: list[MediaContent],
        context: dict[str, str] | None = None,
    ) -> ClientResponse:
        """Send a request with media content (images) for governance analysis.

        This is Proxy Mode with multimodal support - media items are analyzed
        for PII, content safety, biometric data, and document classification.
        """
        return self._run_sync(
            self._async_client.proxy_llm_call_with_media(
                user_token, query, request_type, media, context
            )
        )

    def list_connectors(self) -> list[ConnectorMetadata]:
        """List all available MCP connectors."""
        return self._run_sync(self._async_client.list_connectors())

    def install_connector(self, request: ConnectorInstallRequest) -> None:
        """Install an MCP connector."""
        return self._run_sync(self._async_client.install_connector(request))

    def uninstall_connector(self, connector_name: str) -> None:
        """Uninstall an MCP connector."""
        return self._run_sync(self._async_client.uninstall_connector(connector_name))

    def get_connector(self, connector_id: str) -> ConnectorMetadata:
        """Get details for a specific connector."""
        return self._run_sync(self._async_client.get_connector(connector_id))

    def get_connector_health(self, connector_id: str) -> ConnectorHealthStatus:
        """Get health status of an installed connector."""
        return self._run_sync(self._async_client.get_connector_health(connector_id))

    def query_connector(
        self,
        user_token: str,
        connector_name: str,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Query an MCP connector directly."""
        return self._run_sync(
            self._async_client.query_connector(user_token, connector_name, operation, params)
        )

    def mcp_query(
        self,
        connector: str,
        statement: str,
        options: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Execute a query directly against the MCP connector endpoint.

        This method calls the agent's /mcp/resources/query endpoint which provides:
        - Request-phase policy evaluation (SQLi blocking, PII blocking)
        - Response-phase policy evaluation (PII redaction)
        - PolicyInfo metadata in responses
        """
        return self._run_sync(self._async_client.mcp_query(connector, statement, options))

    def mcp_execute(
        self,
        connector: str,
        statement: str,
        options: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Execute a statement against an MCP connector (alias for mcp_query)."""
        return self._run_sync(self._async_client.mcp_execute(connector, statement, options))

    def mcp_check_input(
        self,
        connector_type: str,
        statement: str,
        operation: str = "execute",
        parameters: dict[str, Any] | None = None,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        user_token: str | None = None,
        content_type: str | None = None,
    ) -> MCPCheckInputResponse:
        """Validate an MCP request against configured policies without executing it."""
        return self._run_sync(
            self._async_client.mcp_check_input(
                connector_type,
                statement,
                operation,
                parameters,
                tool=tool,
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_role=user_role,
                user_token=user_token,
                content_type=content_type,
            )
        )

    def evaluate(self, request: AuthZENRequest) -> AuthZENDecision:
        """Ask whether one subject may perform one action on one resource.

        Synchronous wrapper for :meth:`AxonFlow.evaluate`. See that method for
        the AuthZEN-native contract (ADR-065) and the refusal semantics.
        """
        return self._run_sync(self._async_client.evaluate(request))

    def evaluate_all(self, bulk: AuthZENBulk) -> AuthZENDecision:
        """Ask whether ONE operation is permitted against several preconditions.

        Synchronous wrapper for :meth:`AxonFlow.evaluate_all`. Returns one
        decision, not one per entry: a denied entry denies the operation.
        """
        return self._run_sync(self._async_client.evaluate_all(bulk))

    def decide(self, request: DecideRequest) -> DecideResponse:
        """Ask the PDP for a verdict on a request (``POST /api/v1/decide``).

        Synchronous wrapper for :meth:`AxonFlow.decide`. See that method for the
        decide → fulfill → forward PEP contract (ADR-056, #2563).
        """
        return self._run_sync(self._async_client.decide(request))

    def fulfill_request(
        self,
        decision: DecideResponse,
        statement: str,
    ) -> tuple[str, bool]:
        """Discharge every request-phase ``redact_pii`` obligation via the engine.

        Synchronous wrapper for :meth:`AxonFlow.fulfill_request`. Raises
        :class:`~axonflow.exceptions.ObligationNotFulfillableError` (fail-closed)
        when an obligation cannot be discharged through the engine — never
        redacts locally.
        """
        return self._run_sync(self._async_client.fulfill_request(decision, statement))

    def decide_and_fulfill(
        self,
        request: DecideRequest,
    ) -> tuple[str, str, DecideResponse]:
        """One-call PEP path: decide, then fulfill any request-phase obligation.

        Synchronous wrapper for :meth:`AxonFlow.decide_and_fulfill`.
        """
        return self._run_sync(self._async_client.decide_and_fulfill(request))

    def mcp_check_output(
        self,
        connector_type: str,
        response_data: list[dict[str, Any]] | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        row_count: int = 0,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckOutputResponse:
        """Validate MCP response data against configured policies."""
        return self._run_sync(
            self._async_client.mcp_check_output(
                connector_type,
                response_data,
                message,
                metadata,
                row_count,
                tool=tool,
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_token=user_token,
            )
        )

    def check_tool_input(
        self,
        connector_type: str,
        statement: str,
        operation: str = "execute",
        parameters: dict[str, Any] | None = None,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckInputResponse:
        """Alias for :meth:`mcp_check_input`. Validates tool input against configured policies."""
        return self._run_sync(
            self._async_client.mcp_check_input(
                connector_type,
                statement,
                operation,
                parameters,
                tool=tool,
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_role=user_role,
                user_token=user_token,
            )
        )

    def check_tool_output(
        self,
        connector_type: str,
        response_data: list[dict[str, Any]] | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        row_count: int = 0,
        *,
        tool: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        user_token: str | None = None,
    ) -> MCPCheckOutputResponse:
        """Alias for :meth:`mcp_check_output`. Validates tool output against configured policies."""
        return self._run_sync(
            self._async_client.mcp_check_output(
                connector_type,
                response_data,
                message,
                metadata,
                row_count,
                tool=tool,
                client_id=client_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_token=user_token,
            )
        )

    def generate_plan(
        self,
        query: str,
        domain: str | None = None,
        user_token: str | None = None,
        execution_mode: ExecutionMode | None = None,
    ) -> PlanResponse:
        """Generate a multi-agent execution plan."""
        return self._run_sync(
            self._async_client.generate_plan(query, domain, user_token, execution_mode)
        )

    def execute_plan(
        self,
        plan_id: str,
        user_token: str | None = None,
    ) -> PlanExecutionResponse:
        """Execute a previously generated plan."""
        return self._run_sync(self._async_client.execute_plan(plan_id, user_token))

    def get_plan_status(self, plan_id: str) -> PlanExecutionResponse:
        """Get status of a running or completed plan."""
        return self._run_sync(self._async_client.get_plan_status(plan_id))

    def cancel_plan(
        self,
        plan_id: str,
        reason: str | None = None,
    ) -> CancelPlanResponse:
        """Cancel a running plan."""
        return self._run_sync(self._async_client.cancel_plan(plan_id, reason))

    def update_plan(
        self,
        plan_id: str,
        request: UpdatePlanRequest,
    ) -> UpdatePlanResponse:
        """Update a plan with optimistic concurrency control."""
        return self._run_sync(self._async_client.update_plan(plan_id, request))

    def get_plan_versions(self, plan_id: str) -> PlanVersionsResponse:
        """Get version history of a plan."""
        return self._run_sync(self._async_client.get_plan_versions(plan_id))

    def resume_plan(
        self,
        plan_id: str,
        approved: bool | None = None,
    ) -> ResumePlanResponse:
        """Resume a paused plan."""
        return self._run_sync(self._async_client.resume_plan(plan_id, approved))

    # Gateway Mode sync wrappers

    def get_policy_approved_context(
        self,
        user_token: str,
        query: str,
        data_sources: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyApprovalResult:
        """Perform policy pre-check before making LLM call."""
        return self._run_sync(
            self._async_client.get_policy_approved_context(user_token, query, data_sources, context)
        )

    def pre_check(
        self,
        user_token: str,
        query: str,
        data_sources: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyApprovalResult:
        """Alias for get_policy_approved_context().

        Perform policy pre-check before making LLM call.
        """
        return self._run_sync(
            self._async_client.pre_check(user_token, query, data_sources, context)
        )

    def audit_llm_call(
        self,
        context_id: str,
        response_summary: str,
        provider: str,
        model: str,
        token_usage: TokenUsage,
        latency_ms: int,
        metadata: dict[str, Any] | None = None,
    ) -> AuditResult:
        """Report LLM call details for audit logging."""
        return self._run_sync(
            self._async_client.audit_llm_call(
                context_id, response_summary, provider, model, token_usage, latency_ms, metadata
            )
        )

    def audit_tool_call(
        self,
        request: AuditToolCallRequest,
    ) -> AuditToolCallResponse:
        """Record a non-LLM tool call in the audit trail."""
        return self._run_sync(self._async_client.audit_tool_call(request))

    def list_providers(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[LLMProvider]:
        """List configured LLM providers (synchronous wrapper)."""
        return self._run_sync(
            self._async_client.list_providers(
                provider_type=provider_type, enabled=enabled, page=page, page_size=page_size
            )
        )

    def list_providers_paged(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> LLMProviderListResponse:
        """List providers with pagination metadata (synchronous wrapper)."""
        return self._run_sync(
            self._async_client.list_providers_paged(
                provider_type=provider_type, enabled=enabled, page=page, page_size=page_size
            )
        )

    def list_all_providers(
        self,
        *,
        provider_type: str | None = None,
        enabled: bool | None = None,
        page_size: int = 100,
    ) -> list[LLMProvider]:
        """Walk every page and return all providers (synchronous wrapper)."""
        return self._run_sync(
            self._async_client.list_all_providers(
                provider_type=provider_type, enabled=enabled, page_size=page_size
            )
        )

    # Circuit Breaker Observability sync wrappers

    def get_circuit_breaker_status(self) -> CircuitBreakerStatusResponse:
        """Get all active circuit breaker circuits."""
        return self._run_sync(self._async_client.get_circuit_breaker_status())

    def get_circuit_breaker_history(
        self,
        limit: int | None = None,
    ) -> CircuitBreakerHistoryResponse:
        """Get circuit breaker history for audit trail."""
        return self._run_sync(self._async_client.get_circuit_breaker_history(limit=limit))

    def get_circuit_breaker_config(
        self,
        tenant_id: str | None = None,
    ) -> CircuitBreakerConfig:
        """Get circuit breaker config (global or tenant-specific)."""
        return self._run_sync(self._async_client.get_circuit_breaker_config(tenant_id=tenant_id))

    def update_circuit_breaker_config(
        self,
        config: CircuitBreakerConfigUpdate,
    ) -> dict[str, Any]:
        """Update per-tenant circuit breaker config."""
        return self._run_sync(self._async_client.update_circuit_breaker_config(config))

    # Policy Simulation sync wrappers (Evaluation Tier+)

    def simulate_policies(
        self,
        query: str,
        request_type: str | None = None,
        user: dict[str, Any] | None = None,
        client: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> SimulatePoliciesResponse:
        """Simulate all active policies against input (dry run)."""
        return self._run_sync(
            self._async_client.simulate_policies(
                query,
                request_type=request_type,
                user=user,
                client=client,
                context=context,
            )
        )

    def get_policy_impact_report(
        self,
        policy_id: str,
        inputs: list[dict[str, Any]],
    ) -> ImpactReportResponse:
        """Test a single policy against multiple inputs."""
        return self._run_sync(self._async_client.get_policy_impact_report(policy_id, inputs))

    def detect_policy_conflicts(
        self,
        policy_id: str | None = None,
    ) -> PolicyConflictResponse:
        """Detect conflicts between active policies."""
        return self._run_sync(self._async_client.detect_policy_conflicts(policy_id=policy_id))

    # Policy CRUD sync wrappers

    def list_static_policies(
        self,
        options: ListStaticPoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """List all static policies with optional filtering."""
        return self._run_sync(self._async_client.list_static_policies(options))

    def get_static_policy(self, policy_id: str) -> StaticPolicy:
        """Get a specific static policy by ID."""
        return self._run_sync(self._async_client.get_static_policy(policy_id))

    def create_static_policy(
        self,
        request: CreateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Create a new static policy."""
        return self._run_sync(self._async_client.create_static_policy(request))

    def update_static_policy(
        self,
        policy_id: str,
        request: UpdateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Update an existing static policy."""
        return self._run_sync(self._async_client.update_static_policy(policy_id, request))

    def delete_static_policy(self, policy_id: str) -> None:
        """Delete a static policy."""
        return self._run_sync(self._async_client.delete_static_policy(policy_id))

    def toggle_static_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> StaticPolicy:
        """Toggle a static policy's enabled status."""
        return self._run_sync(self._async_client.toggle_static_policy(policy_id, enabled))

    def get_effective_static_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """Get effective static policies with tier inheritance applied."""
        return self._run_sync(self._async_client.get_effective_static_policies(options))

    def test_pattern(
        self,
        pattern: str,
        test_inputs: list[str],
    ) -> TestPatternResult:
        """Test a regex pattern against sample inputs."""
        return self._run_sync(self._async_client.test_pattern(pattern, test_inputs))

    def get_static_policy_versions(
        self,
        policy_id: str,
    ) -> list[PolicyVersion]:
        """Get version history for a static policy."""
        return self._run_sync(self._async_client.get_static_policy_versions(policy_id))

    # Policy override sync wrappers

    def create_policy_override(
        self,
        policy_id: str,
        request: CreatePolicyOverrideRequest,
    ) -> PolicyOverride:
        """Create an override for a static policy."""
        return self._run_sync(self._async_client.create_policy_override(policy_id, request))

    def delete_policy_override(self, policy_id: str) -> None:
        """Delete an override for a static policy."""
        return self._run_sync(self._async_client.delete_policy_override(policy_id))

    def list_policy_overrides(self) -> list[PolicyOverride]:
        """List all active policy overrides (Enterprise)."""
        return self._run_sync(self._async_client.list_policy_overrides())

    # Dynamic policy sync wrappers

    def list_dynamic_policies(
        self,
        options: ListDynamicPoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """List all dynamic policies with optional filtering."""
        return self._run_sync(self._async_client.list_dynamic_policies(options))

    def get_dynamic_policy(self, policy_id: str) -> DynamicPolicy:
        """Get a specific dynamic policy by ID."""
        return self._run_sync(self._async_client.get_dynamic_policy(policy_id))

    def create_dynamic_policy(
        self,
        request: CreateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Create a new dynamic policy."""
        return self._run_sync(self._async_client.create_dynamic_policy(request))

    def update_dynamic_policy(
        self,
        policy_id: str,
        request: UpdateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Update an existing dynamic policy."""
        return self._run_sync(self._async_client.update_dynamic_policy(policy_id, request))

    def delete_dynamic_policy(self, policy_id: str) -> None:
        """Delete a dynamic policy."""
        return self._run_sync(self._async_client.delete_dynamic_policy(policy_id))

    def toggle_dynamic_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> DynamicPolicy:
        """Toggle a dynamic policy's enabled status."""
        return self._run_sync(self._async_client.toggle_dynamic_policy(policy_id, enabled))

    def get_effective_dynamic_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """Get effective dynamic policies with tier inheritance applied."""
        return self._run_sync(self._async_client.get_effective_dynamic_policies(options))

    # Portal Authentication sync wrappers

    def login_to_portal(self, org_id: str, password: str) -> dict[str, Any]:
        """Login to Customer Portal and store session cookie."""
        return self._run_sync(self._async_client.login_to_portal(org_id, password))

    def logout_from_portal(self) -> None:
        """Logout from Customer Portal and clear session cookie."""
        return self._run_sync(self._async_client.logout_from_portal())

    def is_logged_in(self) -> bool:
        """Check if logged in to Customer Portal."""
        return self._async_client.is_logged_in()

    # Code Governance sync wrappers

    def validate_git_provider(
        self,
        request: ValidateGitProviderRequest,
    ) -> ValidateGitProviderResponse:
        """Validate Git provider credentials before configuration."""
        return self._run_sync(self._async_client.validate_git_provider(request))

    def configure_git_provider(
        self,
        request: ConfigureGitProviderRequest,
    ) -> ConfigureGitProviderResponse:
        """Configure a Git provider for code governance."""
        return self._run_sync(self._async_client.configure_git_provider(request))

    def list_git_providers(self) -> ListGitProvidersResponse:
        """List all configured Git providers for the tenant."""
        return self._run_sync(self._async_client.list_git_providers())

    def delete_git_provider(self, provider_type: GitProviderType) -> None:
        """Delete a configured Git provider."""
        return self._run_sync(self._async_client.delete_git_provider(provider_type))

    def create_pr(self, request: CreatePRRequest) -> CreatePRResponse:
        """Create a Pull Request from LLM-generated code."""
        return self._run_sync(self._async_client.create_pr(request))

    def list_prs(
        self,
        options: ListPRsOptions | None = None,
    ) -> ListPRsResponse:
        """List Pull Requests created through code governance."""
        return self._run_sync(self._async_client.list_prs(options))

    def get_pr(self, pr_id: str) -> PRRecord:
        """Get a specific PR record by ID."""
        return self._run_sync(self._async_client.get_pr(pr_id))

    def sync_pr_status(self, pr_id: str) -> PRRecord:
        """Sync PR status with the Git provider."""
        return self._run_sync(self._async_client.sync_pr_status(pr_id))

    def close_pr(self, pr_id: str, delete_branch: bool = True) -> PRRecord:
        """Close a PR without merging and optionally delete the branch."""
        return self._run_sync(self._async_client.close_pr(pr_id, delete_branch))

    def get_code_governance_metrics(self) -> CodeGovernanceMetrics:
        """Get aggregated code governance metrics."""
        return self._run_sync(self._async_client.get_code_governance_metrics())

    def export_code_governance_data(
        self,
        options: ExportOptions | None = None,
    ) -> ExportResponse:
        """Export code governance data for compliance reporting."""
        return self._run_sync(self._async_client.export_code_governance_data(options))

    def export_code_governance_data_csv(
        self,
        options: ExportOptions | None = None,
    ) -> str:
        """Export code governance data as CSV for compliance reporting."""
        return self._run_sync(self._async_client.export_code_governance_data_csv(options))

    # Workflow Control Plane sync wrappers

    def create_workflow(
        self,
        request: CreateWorkflowRequest,
    ) -> CreateWorkflowResponse:
        """Create a new WCP workflow for tracking external agent execution."""
        return self._run_sync(self._async_client.create_workflow(request))

    def get_workflow(self, workflow_id: str) -> WorkflowStatusResponse:
        """Get the status of a WCP workflow."""
        return self._run_sync(self._async_client.get_workflow(workflow_id))

    def step_gate(
        self,
        workflow_id: str,
        step_id: str,
        request: StepGateRequest,
        *,
        include_prior_output: bool = False,
    ) -> StepGateResponse:
        """Check policy gate for a workflow step."""
        return self._run_sync(
            self._async_client.step_gate(
                workflow_id,
                step_id,
                request,
                include_prior_output=include_prior_output,
            )
        )

    def mark_step_completed(
        self,
        workflow_id: str,
        step_id: str,
        request: MarkStepCompletedRequest | None = None,
    ) -> None:
        """Mark a workflow step as completed."""
        return self._run_sync(self._async_client.mark_step_completed(workflow_id, step_id, request))

    def complete_workflow(self, workflow_id: str) -> None:
        """Mark a workflow as completed."""
        return self._run_sync(self._async_client.complete_workflow(workflow_id))

    def abort_workflow(self, workflow_id: str, reason: str | None = None) -> None:
        """Abort a workflow with an optional reason."""
        return self._run_sync(self._async_client.abort_workflow(workflow_id, reason))

    def fail_workflow(self, workflow_id: str, reason: str | None = None) -> None:
        """Fail a workflow with an optional reason."""
        return self._run_sync(self._async_client.fail_workflow(workflow_id, reason))

    def resume_workflow(self, workflow_id: str) -> None:
        """Resume a paused workflow."""
        return self._run_sync(self._async_client.resume_workflow(workflow_id))

    def list_workflows(
        self,
        options: ListWorkflowsOptions | None = None,
    ) -> ListWorkflowsResponse:
        """List workflows with optional filtering."""
        return self._run_sync(self._async_client.list_workflows(options))

    # WCP Approval sync wrappers (Feature 5)

    def approve_step(
        self,
        workflow_id: str,
        step_id: str,
        comment: str = "",
    ) -> ApproveStepResponse:
        """Approve a workflow step that requires human approval."""
        return self._run_sync(self._async_client.approve_step(workflow_id, step_id, comment))

    def reject_step(
        self,
        workflow_id: str,
        step_id: str,
        reason: str = "",
    ) -> RejectStepResponse:
        """Reject a workflow step that requires human approval."""
        return self._run_sync(self._async_client.reject_step(workflow_id, step_id, reason))

    def get_pending_approvals(
        self,
        limit: int = 20,
    ) -> PendingApprovalsResponse:
        """Get all pending approvals across workflows."""
        return self._run_sync(self._async_client.get_pending_approvals(limit))

    def get_pending_plan_approvals(
        self,
        limit: int = 20,
        plan_id: str | None = None,
    ) -> PendingApprovalsResponse:
        """List pending approvals for MAP-backed workflows (MAP-plane listing).

        See :meth:`AxonFlow.get_pending_plan_approvals` for details.
        """
        return self._run_sync(
            self._async_client.get_pending_plan_approvals(limit=limit, plan_id=plan_id)
        )

    # HITL Queue API sync wrappers (Enterprise)

    def list_hitl_queue(
        self,
        opts: HITLQueueListOptions | None = None,
    ) -> HITLQueueListResponse:
        """List approval requests in the HITL queue."""
        return self._run_sync(self._async_client.list_hitl_queue(opts))

    def create_hitl_request(
        self,
        request: HITLCreateInput,
    ) -> HITLApprovalRequest:
        """Create a HITL approval request in the queue (sync).

        See :py:meth:`AxonFlow.create_hitl_request` for full semantics.
        """
        return self._run_sync(self._async_client.create_hitl_request(request))

    def get_hitl_request(self, request_id: str) -> HITLApprovalRequest:
        """Get a specific HITL approval request."""
        return self._run_sync(self._async_client.get_hitl_request(request_id))

    def approve_hitl_request(
        self,
        request_id: str,
        review: HITLReviewInput,
    ) -> None:
        """Approve a pending HITL approval request."""
        return self._run_sync(self._async_client.approve_hitl_request(request_id, review))

    def reject_hitl_request(
        self,
        request_id: str,
        review: HITLReviewInput,
    ) -> None:
        """Reject a pending HITL approval request."""
        return self._run_sync(self._async_client.reject_hitl_request(request_id, review))

    def get_hitl_stats(self) -> HITLStats:
        """Get HITL queue dashboard statistics."""
        return self._run_sync(self._async_client.get_hitl_stats())

    # Plan Rollback sync wrapper (Feature 7)

    def rollback_plan(
        self,
        plan_id: str,
        target_version: int,
    ) -> RollbackPlanResponse:
        """Rollback a plan to a previous version."""
        return self._run_sync(self._async_client.rollback_plan(plan_id, target_version))

    # Webhook CRUD sync wrappers (Feature 7)

    def create_webhook(
        self,
        url: str,
        events: list[str],
        secret: str = "",
        active: bool = True,
    ) -> WebhookSubscription:
        """Create a webhook subscription."""
        return self._run_sync(self._async_client.create_webhook(url, events, secret, active))

    def get_webhook(self, webhook_id: str) -> WebhookSubscription:
        """Get a webhook subscription by ID."""
        return self._run_sync(self._async_client.get_webhook(webhook_id))

    def update_webhook(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        secret: str | None = None,
        active: bool | None = None,
        description: str | None = None,
    ) -> WebhookSubscription:
        """Update a webhook subscription."""
        return self._run_sync(
            self._async_client.update_webhook(
                webhook_id,
                url=url,
                events=events,
                secret=secret,
                active=active,
                description=description,
            )
        )

    def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook subscription."""
        return self._run_sync(self._async_client.delete_webhook(webhook_id))

    def list_webhooks(self) -> ListWebhooksResponse:
        """List all webhook subscriptions."""
        return self._run_sync(self._async_client.list_webhooks())

    # Unified Execution Tracking sync wrappers

    def get_execution_status(self, execution_id: str) -> ExecutionStatus:
        """Get unified execution status for both MAP plans and WCP workflows."""
        return self._run_sync(self._async_client.get_execution_status(execution_id))

    def list_unified_executions(
        self,
        request: UnifiedListExecutionsRequest | None = None,
    ) -> UnifiedListExecutionsResponse:
        """List unified executions (both MAP plans and WCP workflows)."""
        return self._run_sync(self._async_client.list_unified_executions(request))

    def cancel_execution(
        self,
        execution_id: str,
        reason: str | None = None,
    ) -> None:
        """Cancel a unified execution (MAP plan or WCP workflow)."""
        return self._run_sync(self._async_client.cancel_execution(execution_id, reason))

    def stream_execution_status(
        self,
        execution_id: str,
        *,
        timeout: float | None = None,
    ) -> Iterator[ExecutionStatus]:
        """Stream real-time execution status updates via SSE (synchronous).

        Connects to the SSE streaming endpoint and yields ExecutionStatus objects
        as they arrive. The stream ends when a terminal status is received.

        Args:
            execution_id: The execution ID to stream updates for
            timeout: Optional overall timeout in seconds

        Yields:
            ExecutionStatus objects with real-time progress updates

        Example:
            >>> for status in client.stream_execution_status('exec_123'):
            ...     print(f"Status: {status.status}, Progress: {status.progress_percent}%")
        """

        async def _collect() -> list[ExecutionStatus]:
            results: list[ExecutionStatus] = []
            async for status in self._async_client.stream_execution_status(
                execution_id, timeout=timeout
            ):
                results.append(status)
            return results

        results = self._run_sync(_collect())
        yield from results

    # Execution Replay sync wrappers

    def list_executions(
        self,
        options: ListExecutionsOptions | None = None,
    ) -> ListExecutionsResponse:
        """List workflow executions with optional filtering."""
        return self._run_sync(self._async_client.list_executions(options))

    def get_execution(self, execution_id: str) -> ExecutionDetail:
        """Get a complete execution record including summary and all steps."""
        return self._run_sync(self._async_client.get_execution(execution_id))

    def get_execution_steps(self, execution_id: str) -> list[ExecutionSnapshot]:
        """Get all step snapshots for an execution."""
        return self._run_sync(self._async_client.get_execution_steps(execution_id))

    def get_execution_timeline(self, execution_id: str) -> list[TimelineEntry]:
        """Get timeline view of execution for visualization."""
        return self._run_sync(self._async_client.get_execution_timeline(execution_id))

    def export_execution(
        self,
        execution_id: str,
        options: ExecutionExportOptions | None = None,
    ) -> dict[str, Any]:
        """Export a complete execution record for compliance or archival."""
        return self._run_sync(self._async_client.export_execution(execution_id, options))

    def delete_execution(self, execution_id: str) -> None:
        """Delete an execution and all associated step snapshots."""
        return self._run_sync(self._async_client.delete_execution(execution_id))

    # Cost Controls sync wrappers

    def create_budget(self, request: CreateBudgetRequest) -> Budget:
        """Create a new budget."""
        return self._run_sync(self._async_client.create_budget(request))

    def get_budget(self, budget_id: str) -> Budget:
        """Get a budget by ID."""
        return self._run_sync(self._async_client.get_budget(budget_id))

    def list_budgets(self, options: ListBudgetsOptions | None = None) -> BudgetsResponse:
        """List all budgets."""
        return self._run_sync(self._async_client.list_budgets(options))

    def update_budget(self, budget_id: str, request: UpdateBudgetRequest) -> Budget:
        """Update an existing budget."""
        return self._run_sync(self._async_client.update_budget(budget_id, request))

    def delete_budget(self, budget_id: str) -> None:
        """Delete a budget."""
        return self._run_sync(self._async_client.delete_budget(budget_id))

    def get_budget_status(self, budget_id: str) -> BudgetStatus:
        """Get the current status of a budget."""
        return self._run_sync(self._async_client.get_budget_status(budget_id))

    def get_budget_alerts(self, budget_id: str) -> BudgetAlertsResponse:
        """Get alerts for a budget."""
        return self._run_sync(self._async_client.get_budget_alerts(budget_id))

    def check_budget(self, request: BudgetCheckRequest) -> BudgetDecision:
        """Perform a pre-flight budget check."""
        return self._run_sync(self._async_client.check_budget(request))

    def get_usage_summary(self, period: str | None = None) -> UsageSummary:
        """Get usage summary for a period."""
        return self._run_sync(self._async_client.get_usage_summary(period))

    def get_usage_breakdown(self, group_by: str, period: str | None = None) -> UsageBreakdown:
        """Get usage breakdown by a grouping dimension."""
        return self._run_sync(self._async_client.get_usage_breakdown(group_by, period))

    def list_usage_records(
        self, options: ListUsageRecordsOptions | None = None
    ) -> UsageRecordsResponse:
        """List usage records."""
        return self._run_sync(self._async_client.list_usage_records(options))

    def get_pricing(
        self, provider: str | None = None, model: str | None = None
    ) -> PricingListResponse:
        """Get pricing information for models."""
        return self._run_sync(self._async_client.get_pricing(provider, model))

    # Media Governance Config sync wrappers

    def get_media_governance_config(self) -> MediaGovernanceConfig:
        """Get the media governance configuration for the current tenant."""
        return self._run_sync(self._async_client.get_media_governance_config())

    def update_media_governance_config(
        self,
        request: UpdateMediaGovernanceConfigRequest,
    ) -> MediaGovernanceConfig:
        """Update the media governance configuration for the current tenant."""
        return self._run_sync(self._async_client.update_media_governance_config(request))

    def get_media_governance_status(self) -> MediaGovernanceStatus:
        """Get the platform-level media governance status."""
        return self._run_sync(self._async_client.get_media_governance_status())

    # =========================================================================
    # MAS FEAT Compliance sync wrappers (Enterprise)
    # =========================================================================

    def masfeat_register_system(
        self,
        system_id: str,
        system_name: str,
        use_case: str,
        owner_team: str,
        customer_impact: int,
        model_complexity: int,
        human_reliance: int,
        *,
        description: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Register an AI system in the MAS FEAT registry."""
        return self._run_sync(
            self._async_client.masfeat_register_system(
                system_id,
                system_name,
                use_case,
                owner_team,
                customer_impact,
                model_complexity,
                human_reliance,
                description=description,
                technical_owner=technical_owner,
                business_owner=business_owner,
                metadata=metadata,
            )
        )

    def masfeat_get_system(self, system_id: str) -> AISystemRegistry:
        """Get an AI system from the registry."""
        return self._run_sync(self._async_client.masfeat_get_system(system_id))

    def masfeat_update_system(
        self,
        system_id: str,
        *,
        system_name: str | None = None,
        description: str | None = None,
        owner_team: str | None = None,
        technical_owner: str | None = None,
        business_owner: str | None = None,
        customer_impact: int | None = None,
        model_complexity: int | None = None,
        human_reliance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AISystemRegistry:
        """Update an AI system in the registry."""
        return self._run_sync(
            self._async_client.masfeat_update_system(
                system_id,
                system_name=system_name,
                description=description,
                owner_team=owner_team,
                technical_owner=technical_owner,
                business_owner=business_owner,
                customer_impact=customer_impact,
                model_complexity=model_complexity,
                human_reliance=human_reliance,
                metadata=metadata,
            )
        )

    def masfeat_list_systems(
        self,
        *,
        status: str | None = None,
        use_case: str | None = None,
        materiality: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AISystemRegistry]:
        """List AI systems in the registry."""
        return self._run_sync(
            self._async_client.masfeat_list_systems(
                status=status,
                use_case=use_case,
                materiality=materiality,
                limit=limit,
                offset=offset,
            )
        )

    def masfeat_activate_system(self, system_id: str) -> AISystemRegistry:
        """Activate an AI system."""
        return self._run_sync(self._async_client.masfeat_activate_system(system_id))

    def masfeat_retire_system(self, system_id: str) -> AISystemRegistry:
        """Retire an AI system."""
        return self._run_sync(self._async_client.masfeat_retire_system(system_id))

    def masfeat_get_registry_summary(self) -> RegistrySummary:
        """Get registry summary."""
        return self._run_sync(self._async_client.masfeat_get_registry_summary())

    def masfeat_create_assessment(
        self,
        system_id: str,
        *,
        assessment_type: str = "periodic",
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Create a FEAT assessment."""
        return self._run_sync(
            self._async_client.masfeat_create_assessment(
                system_id, assessment_type=assessment_type, assessors=assessors
            )
        )

    def masfeat_get_assessment(self, assessment_id: str) -> FEATAssessment:
        """Get a FEAT assessment."""
        return self._run_sync(self._async_client.masfeat_get_assessment(assessment_id))

    def masfeat_update_assessment(
        self,
        assessment_id: str,
        *,
        fairness_score: int | None = None,
        ethics_score: int | None = None,
        accountability_score: int | None = None,
        transparency_score: int | None = None,
        fairness_details: dict[str, Any] | None = None,
        ethics_details: dict[str, Any] | None = None,
        accountability_details: dict[str, Any] | None = None,
        transparency_details: dict[str, Any] | None = None,
        findings: list[Finding] | None = None,
        recommendations: list[str] | None = None,
        assessors: list[str] | None = None,
    ) -> FEATAssessment:
        """Update a FEAT assessment."""
        return self._run_sync(
            self._async_client.masfeat_update_assessment(
                assessment_id,
                fairness_score=fairness_score,
                ethics_score=ethics_score,
                accountability_score=accountability_score,
                transparency_score=transparency_score,
                fairness_details=fairness_details,
                ethics_details=ethics_details,
                accountability_details=accountability_details,
                transparency_details=transparency_details,
                findings=findings,
                recommendations=recommendations,
                assessors=assessors,
            )
        )

    def masfeat_list_assessments(
        self,
        *,
        system_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[FEATAssessment]:
        """List FEAT assessments."""
        return self._run_sync(
            self._async_client.masfeat_list_assessments(
                system_id=system_id, status=status, limit=limit, offset=offset
            )
        )

    def masfeat_submit_assessment(self, assessment_id: str) -> FEATAssessment:
        """Submit a FEAT assessment for approval."""
        return self._run_sync(self._async_client.masfeat_submit_assessment(assessment_id))

    def masfeat_approve_assessment(
        self,
        assessment_id: str,
        approved_by: str,
        *,
        comments: str | None = None,
    ) -> FEATAssessment:
        """Approve a FEAT assessment."""
        return self._run_sync(
            self._async_client.masfeat_approve_assessment(
                assessment_id, approved_by, comments=comments
            )
        )

    def masfeat_reject_assessment(
        self,
        assessment_id: str,
        rejected_by: str,
        reason: str,
    ) -> FEATAssessment:
        """Reject a FEAT assessment."""
        return self._run_sync(
            self._async_client.masfeat_reject_assessment(assessment_id, rejected_by, reason)
        )

    def masfeat_get_kill_switch(self, system_id: str) -> KillSwitch:
        """Get kill switch configuration."""
        return self._run_sync(self._async_client.masfeat_get_kill_switch(system_id))

    def masfeat_configure_kill_switch(
        self,
        system_id: str,
        *,
        accuracy_threshold: float | None = None,
        bias_threshold: float | None = None,
        error_rate_threshold: float | None = None,
        auto_trigger_enabled: bool | None = None,
    ) -> KillSwitch:
        """Configure kill switch thresholds."""
        return self._run_sync(
            self._async_client.masfeat_configure_kill_switch(
                system_id,
                accuracy_threshold=accuracy_threshold,
                bias_threshold=bias_threshold,
                error_rate_threshold=error_rate_threshold,
                auto_trigger_enabled=auto_trigger_enabled,
            )
        )

    def masfeat_check_kill_switch(
        self,
        system_id: str,
        accuracy: float,
        *,
        bias_score: float | None = None,
        error_rate: float | None = None,
    ) -> KillSwitch:
        """Check current metrics against kill switch thresholds."""
        return self._run_sync(
            self._async_client.masfeat_check_kill_switch(
                system_id, accuracy, bias_score=bias_score, error_rate=error_rate
            )
        )

    def masfeat_trigger_kill_switch(
        self,
        system_id: str,
        reason: str,
        *,
        triggered_by: str | None = None,
    ) -> KillSwitch:
        """Manually trigger the kill switch."""
        return self._run_sync(
            self._async_client.masfeat_trigger_kill_switch(
                system_id, reason, triggered_by=triggered_by
            )
        )

    def masfeat_restore_kill_switch(
        self,
        system_id: str,
        reason: str,
        *,
        restored_by: str | None = None,
    ) -> KillSwitch:
        """Restore the kill switch."""
        return self._run_sync(
            self._async_client.masfeat_restore_kill_switch(
                system_id, reason, restored_by=restored_by
            )
        )

    def masfeat_enable_kill_switch(self, system_id: str) -> KillSwitch:
        """Enable the kill switch."""
        return self._run_sync(self._async_client.masfeat_enable_kill_switch(system_id))

    def masfeat_disable_kill_switch(
        self,
        system_id: str,
        *,
        reason: str | None = None,
    ) -> KillSwitch:
        """Disable the kill switch."""
        return self._run_sync(
            self._async_client.masfeat_disable_kill_switch(system_id, reason=reason)
        )

    def masfeat_get_kill_switch_history(
        self,
        system_id: str,
        *,
        limit: int | None = None,
    ) -> list[KillSwitchEvent]:
        """Get kill switch event history."""
        return self._run_sync(
            self._async_client.masfeat_get_kill_switch_history(system_id, limit=limit)
        )
