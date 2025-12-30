"""AxonFlow SDK Main Client.

The primary interface for interacting with AxonFlow governance platform.
Supports both async and sync usage patterns.

Example:
    >>> from axonflow import AxonFlow
    >>>
    >>> # Async usage (enterprise with authentication)
    >>> async with AxonFlow(agent_url="...", client_id="...", client_secret="...") as client:
    ...     result = await client.execute_query("user-token", "What is AI?", "chat")
    ...     print(result.data)
    >>>
    >>> # Async usage (community/self-hosted - no auth required)
    >>> async with AxonFlow(agent_url="http://localhost:8080") as client:
    ...     result = await client.execute_query("user-token", "What is AI?", "chat")
    ...     print(result.data)
    >>>
    >>> # Sync usage
    >>> client = AxonFlow.sync(agent_url="...", client_id="...", client_secret="...")
    >>> result = client.execute_query("user-token", "What is AI?", "chat")
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from cachetools import TTLCache
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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
from axonflow.exceptions import (
    AuthenticationError,
    AxonFlowError,
    ConnectionError,
    PolicyViolationError,
    TimeoutError,
)
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
from axonflow.types import (
    AuditResult,
    AxonFlowConfig,
    CacheConfig,
    ClientRequest,
    ClientResponse,
    ConnectorInstallRequest,
    ConnectorMetadata,
    ConnectorResponse,
    Mode,
    PlanExecutionResponse,
    PlanResponse,
    PlanStep,
    PolicyApprovalResult,
    RateLimitInfo,
    RetryConfig,
    TokenUsage,
)

if TYPE_CHECKING:
    from types import TracebackType

logger = structlog.get_logger(__name__)


def _parse_datetime(value: str) -> datetime:
    """Parse ISO format datetime string.

    Python 3.9's fromisoformat() doesn't handle 'Z' suffix for UTC.
    This helper replaces 'Z' with '+00:00' for compatibility.

    Also handles nanosecond precision (9 digits) by truncating to microseconds (6 digits)
    since Python's fromisoformat() only supports up to 6 fractional digits.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # Python's fromisoformat only supports up to 6 fractional digits (microseconds)
    # Truncate nanoseconds (9 digits) to microseconds (6 digits) if needed
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)

    return datetime.fromisoformat(value)


class AxonFlow:
    """Main AxonFlow client for AI governance.

    This client provides async-first API for interacting with AxonFlow Agent.
    All methods are async by default, with sync wrappers available via `.sync()`.

    Attributes:
        config: Client configuration
    """

    __slots__ = ("_config", "_http_client", "_map_http_client", "_cache", "_logger")

    def __init__(
        self,
        agent_url: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        license_key: str | None = None,
        mode: Mode | str = Mode.PRODUCTION,
        debug: bool = False,
        timeout: float = 60.0,
        map_timeout: float = 120.0,
        insecure_skip_verify: bool = False,
        retry_config: RetryConfig | None = None,
        cache_enabled: bool = True,
        cache_ttl: float = 60.0,
        cache_max_size: int = 1000,
    ) -> None:
        """Initialize AxonFlow client.

        Args:
            agent_url: AxonFlow Agent URL
            client_id: Client ID (optional for community/self-hosted mode)
            client_secret: Client secret (optional for community/self-hosted mode)
            license_key: Optional license key for organization-level auth
            mode: Operation mode (production or sandbox)
            debug: Enable debug logging
            timeout: Request timeout in seconds
            map_timeout: Timeout for MAP operations in seconds (default: 120s)
                        MAP operations involve multiple LLM calls and need longer timeouts
            insecure_skip_verify: Skip TLS verification (dev only)
            retry_config: Retry configuration
            cache_enabled: Enable response caching
            cache_ttl: Cache TTL in seconds
            cache_max_size: Maximum cache entries

        Note:
            For community/self-hosted deployments, client_id and client_secret can be omitted.
            The SDK will work without authentication headers in this mode.
        """
        if isinstance(mode, str):
            mode = Mode(mode)

        self._config = AxonFlowConfig(
            agent_url=agent_url.rstrip("/"),
            client_id=client_id,
            client_secret=client_secret,
            license_key=license_key,
            mode=mode,
            debug=debug,
            timeout=timeout,
            map_timeout=map_timeout,
            insecure_skip_verify=insecure_skip_verify,
            retry=retry_config or RetryConfig(),
            cache=CacheConfig(enabled=cache_enabled, ttl=cache_ttl, max_size=cache_max_size),
        )

        # Configure SSL verification
        verify_ssl: bool = not insecure_skip_verify

        # Build headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        # Add authentication headers only when credentials are provided
        # For community/self-hosted mode, these can be omitted
        if client_secret:
            headers["X-Client-Secret"] = client_secret
        if client_id:
            headers["X-Tenant-ID"] = client_id  # client_id is used as tenant ID for policy APIs
        if license_key:
            headers["X-License-Key"] = license_key

        # Initialize HTTP client
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=verify_ssl,
            headers=headers,
        )

        # Initialize MAP HTTP client with longer timeout
        self._map_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(map_timeout),
            verify=verify_ssl,
            headers=headers,
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

        if debug:
            self._logger.info(
                "AxonFlow client initialized",
                agent_url=agent_url,
            )

    @property
    def config(self) -> AxonFlowConfig:
        """Get client configuration."""
        return self._config

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
        agent_url: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> SyncAxonFlow:
        """Create a synchronous client wrapper.

        Example:
            >>> # Enterprise mode with authentication
            >>> client = AxonFlow.sync(agent_url="...", client_id="...", client_secret="...")
            >>> result = client.execute_query("token", "query", "chat")
            >>>
            >>> # Community/self-hosted mode (no auth required)
            >>> client = AxonFlow.sync(agent_url="http://localhost:8080")
            >>> result = client.execute_query("token", "query", "chat")
        """
        return SyncAxonFlow(cls(agent_url, client_id, client_secret, **kwargs))

    @classmethod
    def sandbox(cls, api_key: str = "demo-key") -> AxonFlow:
        """Create a sandbox client for testing.

        Args:
            api_key: Optional API key (defaults to demo-key)

        Returns:
            Configured AxonFlow client for sandbox environment
        """
        return cls(
            agent_url="https://staging-eu.getaxonflow.com",
            client_id=api_key,
            client_secret=api_key,
            mode=Mode.SANDBOX,
            debug=True,
        )

    def _get_cache_key(self, request_type: str, query: str, user_token: str) -> str:
        """Generate cache key for a request."""
        key = f"{request_type}:{query}:{user_token}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Agent."""
        url = f"{self._config.agent_url}{path}"

        try:
            if self._config.retry.enabled:
                response = await self._request_with_retry(method, url, json_data)
            else:
                response = await self._http_client.request(method, url, json=json_data)

            response.raise_for_status()
            # Handle 204 No Content (e.g., DELETE responses)
            if response.status_code == 204:  # noqa: PLR2004
                return None  # type: ignore[return-value]
            return response.json()  # type: ignore[no-any-return]

        except httpx.ConnectError as e:
            msg = f"Failed to connect to AxonFlow Agent: {e}"
            raise ConnectionError(msg) from e
        except httpx.TimeoutException as e:
            msg = f"Request timed out: {e}"
            raise TimeoutError(msg) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:  # noqa: PLR2004
                msg = "Invalid credentials"
                raise AuthenticationError(msg) from e
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
    ) -> httpx.Response:
        """Make request with retry logic."""

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
            return await self._http_client.request(method, url, json=json_data)

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
        url = f"{self._config.agent_url}{path}"

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

    async def execute_query(
        self,
        user_token: str,
        query: str,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Execute a query through AxonFlow with policy enforcement.

        Args:
            user_token: User authentication token
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
        # Check cache
        if self._cache is not None:
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

        # Cache successful responses
        if self._cache is not None and response.success and cache_key:
            self._cache[cache_key] = response

        return response

    async def list_connectors(self) -> list[ConnectorMetadata]:
        """List all available MCP connectors.

        Returns:
            List of connector metadata
        """
        response = await self._request("GET", "/api/connectors")
        return [ConnectorMetadata.model_validate(c) for c in response]

    async def install_connector(self, request: ConnectorInstallRequest) -> None:
        """Install an MCP connector.

        Args:
            request: Connector installation request
        """
        await self._request(
            "POST",
            "/api/connectors/install",
            json_data=request.model_dump(),
        )

        if self._config.debug:
            self._logger.info("Connector installed", name=request.name)

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
            operation: Operation to perform
            params: Operation parameters

        Returns:
            ConnectorResponse with results
        """
        request_data: dict[str, Any] = {
            "client_id": self._config.client_id,
            "user_token": user_token,
            "connector": connector_name,
            "operation": operation,
            "parameters": params or {},
        }

        if self._config.license_key:
            request_data["license_key"] = self._config.license_key

        response = await self._request(
            "POST",
            "/mcp/resources/query",
            json_data=request_data,
        )

        return ConnectorResponse.model_validate(response)

    async def generate_plan(
        self,
        query: str,
        domain: str | None = None,
        user_token: str | None = None,
    ) -> PlanResponse:
        """Generate a multi-agent execution plan.

        Args:
            query: Natural language query describing the task
            domain: Optional domain hint (travel, healthcare, etc.)
            user_token: Optional user token for authentication (defaults to client_id)

        Returns:
            PlanResponse with generated plan

        Note:
            This uses map_timeout (default 120s) as MAP operations involve
            multiple LLM calls and can take 30-60+ seconds.
        """
        context = {"domain": domain} if domain else {}

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
        return PlanResponse(
            plan_id=plan_id,
            steps=steps,
            domain=response.data.get("domain", domain or "generic")
            if response.data and isinstance(response.data, dict)
            else (domain or "generic"),
            complexity=response.data.get("complexity", 0)
            if response.data and isinstance(response.data, dict)
            else 0,
            parallel=response.data.get("parallel", False)
            if response.data and isinstance(response.data, dict)
            else False,
            metadata=response.metadata,
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

        return PlanExecutionResponse(
            plan_id=plan_id,
            status="completed" if response.success else "failed",
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
        response = await self._request("GET", f"/api/plans/{plan_id}")
        return PlanExecutionResponse.model_validate(response)

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
        request_body = {
            "user_token": user_token,
            "client_id": self._config.client_id,
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
            approved_data=response.get("approved_data", {}),
            policies=response.get("policies", []),
            rate_limit_info=rate_limit,
            expires_at=_parse_datetime(response["expires_at"]),
            block_reason=response.get("block_reason"),
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
        request_body = {
            "context_id": context_id,
            "client_id": self._config.client_id,
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
            if options.category:
                params.append(f"category={options.category.value}")
            if options.tier:
                params.append(f"tier={options.tier.value}")
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

        path = "/api/v1/policies"
        if params:
            path = f"{path}?{'&'.join(params)}"

        if self._config.debug:
            self._logger.debug("Listing dynamic policies", path=path)

        response = await self._request("GET", path)
        return [DynamicPolicy.model_validate(p) for p in response]

    async def get_dynamic_policy(self, policy_id: str) -> DynamicPolicy:
        """Get a specific dynamic policy by ID.

        Args:
            policy_id: Policy ID

        Returns:
            The dynamic policy
        """
        if self._config.debug:
            self._logger.debug("Getting dynamic policy", policy_id=policy_id)

        response = await self._request("GET", f"/api/v1/policies/{policy_id}")
        return DynamicPolicy.model_validate(response)

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

        response = await self._request(
            "POST",
            "/api/v1/policies",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return DynamicPolicy.model_validate(response)

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

        response = await self._request(
            "PUT",
            f"/api/v1/policies/{policy_id}",
            json_data=request.model_dump(exclude_none=True, by_alias=True),
        )
        return DynamicPolicy.model_validate(response)

    async def delete_dynamic_policy(self, policy_id: str) -> None:
        """Delete a dynamic policy.

        Args:
            policy_id: Policy ID
        """
        if self._config.debug:
            self._logger.debug("Deleting dynamic policy", policy_id=policy_id)

        await self._request("DELETE", f"/api/v1/policies/{policy_id}")

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

        response = await self._request(
            "PATCH",
            f"/api/v1/policies/{policy_id}",
            json_data={"enabled": enabled},
        )
        return DynamicPolicy.model_validate(response)

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

        path = "/api/v1/policies/effective"
        if query_params:
            path = f"{path}?{'&'.join(query_params)}"

        if self._config.debug:
            self._logger.debug("Getting effective dynamic policies", path=path)

        response = await self._request("GET", path)
        return [DynamicPolicy.model_validate(p) for p in response]

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

        response = await self._request(
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

        response = await self._request(
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

        response = await self._request("GET", "/api/v1/code-governance/git-providers")
        return ListGitProvidersResponse.model_validate(response)

    async def delete_git_provider(self, provider_type: GitProviderType) -> None:
        """Delete a configured Git provider.

        Args:
            provider_type: Provider type to delete
        """
        if self._config.debug:
            self._logger.debug("Deleting Git provider", provider_type=provider_type.value)

        path = f"/api/v1/code-governance/git-providers/{provider_type.value}"
        await self._request("DELETE", path)

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

        response = await self._request(
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

        response = await self._request("GET", path)
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

        response = await self._request("GET", f"/api/v1/code-governance/prs/{pr_id}")
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

        response = await self._request("POST", f"/api/v1/code-governance/prs/{pr_id}/sync")
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

        response = await self._request("GET", "/api/v1/code-governance/metrics")
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

        response = await self._request("GET", path)
        return ExportResponse.model_validate(response)


class SyncAxonFlow:
    """Synchronous wrapper for AxonFlow client.

    Wraps all async methods for synchronous usage.
    """

    __slots__ = ("_async_client", "_loop")

    def __init__(self, async_client: AxonFlow) -> None:
        self._async_client = async_client
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

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
        """Close the client."""
        self._get_loop().run_until_complete(self._async_client.close())

    @property
    def config(self) -> AxonFlowConfig:
        """Get client configuration."""
        return self._async_client.config

    def health_check(self) -> bool:
        """Check if AxonFlow Agent is healthy."""
        return self._get_loop().run_until_complete(self._async_client.health_check())

    def execute_query(
        self,
        user_token: str,
        query: str,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Execute a query through AxonFlow."""
        return self._get_loop().run_until_complete(
            self._async_client.execute_query(user_token, query, request_type, context)
        )

    def list_connectors(self) -> list[ConnectorMetadata]:
        """List all available MCP connectors."""
        return self._get_loop().run_until_complete(self._async_client.list_connectors())

    def install_connector(self, request: ConnectorInstallRequest) -> None:
        """Install an MCP connector."""
        return self._get_loop().run_until_complete(self._async_client.install_connector(request))

    def query_connector(
        self,
        user_token: str,
        connector_name: str,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        """Query an MCP connector directly."""
        return self._get_loop().run_until_complete(
            self._async_client.query_connector(user_token, connector_name, operation, params)
        )

    def generate_plan(
        self,
        query: str,
        domain: str | None = None,
        user_token: str | None = None,
    ) -> PlanResponse:
        """Generate a multi-agent execution plan."""
        return self._get_loop().run_until_complete(
            self._async_client.generate_plan(query, domain, user_token)
        )

    def execute_plan(
        self,
        plan_id: str,
        user_token: str | None = None,
    ) -> PlanExecutionResponse:
        """Execute a previously generated plan."""
        return self._get_loop().run_until_complete(
            self._async_client.execute_plan(plan_id, user_token)
        )

    def get_plan_status(self, plan_id: str) -> PlanExecutionResponse:
        """Get status of a running or completed plan."""
        return self._get_loop().run_until_complete(self._async_client.get_plan_status(plan_id))

    # Gateway Mode sync wrappers

    def get_policy_approved_context(
        self,
        user_token: str,
        query: str,
        data_sources: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PolicyApprovalResult:
        """Perform policy pre-check before making LLM call."""
        return self._get_loop().run_until_complete(
            self._async_client.get_policy_approved_context(user_token, query, data_sources, context)
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
        return self._get_loop().run_until_complete(
            self._async_client.audit_llm_call(
                context_id, response_summary, provider, model, token_usage, latency_ms, metadata
            )
        )

    # Policy CRUD sync wrappers

    def list_static_policies(
        self,
        options: ListStaticPoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """List all static policies with optional filtering."""
        return self._get_loop().run_until_complete(self._async_client.list_static_policies(options))

    def get_static_policy(self, policy_id: str) -> StaticPolicy:
        """Get a specific static policy by ID."""
        return self._get_loop().run_until_complete(self._async_client.get_static_policy(policy_id))

    def create_static_policy(
        self,
        request: CreateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Create a new static policy."""
        return self._get_loop().run_until_complete(self._async_client.create_static_policy(request))

    def update_static_policy(
        self,
        policy_id: str,
        request: UpdateStaticPolicyRequest,
    ) -> StaticPolicy:
        """Update an existing static policy."""
        return self._get_loop().run_until_complete(
            self._async_client.update_static_policy(policy_id, request)
        )

    def delete_static_policy(self, policy_id: str) -> None:
        """Delete a static policy."""
        return self._get_loop().run_until_complete(
            self._async_client.delete_static_policy(policy_id)
        )

    def toggle_static_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> StaticPolicy:
        """Toggle a static policy's enabled status."""
        return self._get_loop().run_until_complete(
            self._async_client.toggle_static_policy(policy_id, enabled)
        )

    def get_effective_static_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[StaticPolicy]:
        """Get effective static policies with tier inheritance applied."""
        return self._get_loop().run_until_complete(
            self._async_client.get_effective_static_policies(options)
        )

    def test_pattern(
        self,
        pattern: str,
        test_inputs: list[str],
    ) -> TestPatternResult:
        """Test a regex pattern against sample inputs."""
        return self._get_loop().run_until_complete(
            self._async_client.test_pattern(pattern, test_inputs)
        )

    def get_static_policy_versions(
        self,
        policy_id: str,
    ) -> list[PolicyVersion]:
        """Get version history for a static policy."""
        return self._get_loop().run_until_complete(
            self._async_client.get_static_policy_versions(policy_id)
        )

    # Policy override sync wrappers

    def create_policy_override(
        self,
        policy_id: str,
        request: CreatePolicyOverrideRequest,
    ) -> PolicyOverride:
        """Create an override for a static policy."""
        return self._get_loop().run_until_complete(
            self._async_client.create_policy_override(policy_id, request)
        )

    def delete_policy_override(self, policy_id: str) -> None:
        """Delete an override for a static policy."""
        return self._get_loop().run_until_complete(
            self._async_client.delete_policy_override(policy_id)
        )

    def list_policy_overrides(self) -> list[PolicyOverride]:
        """List all active policy overrides (Enterprise)."""
        return self._get_loop().run_until_complete(self._async_client.list_policy_overrides())

    # Dynamic policy sync wrappers

    def list_dynamic_policies(
        self,
        options: ListDynamicPoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """List all dynamic policies with optional filtering."""
        return self._get_loop().run_until_complete(
            self._async_client.list_dynamic_policies(options)
        )

    def get_dynamic_policy(self, policy_id: str) -> DynamicPolicy:
        """Get a specific dynamic policy by ID."""
        return self._get_loop().run_until_complete(self._async_client.get_dynamic_policy(policy_id))

    def create_dynamic_policy(
        self,
        request: CreateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Create a new dynamic policy."""
        return self._get_loop().run_until_complete(
            self._async_client.create_dynamic_policy(request)
        )

    def update_dynamic_policy(
        self,
        policy_id: str,
        request: UpdateDynamicPolicyRequest,
    ) -> DynamicPolicy:
        """Update an existing dynamic policy."""
        return self._get_loop().run_until_complete(
            self._async_client.update_dynamic_policy(policy_id, request)
        )

    def delete_dynamic_policy(self, policy_id: str) -> None:
        """Delete a dynamic policy."""
        return self._get_loop().run_until_complete(
            self._async_client.delete_dynamic_policy(policy_id)
        )

    def toggle_dynamic_policy(
        self,
        policy_id: str,
        enabled: bool,
    ) -> DynamicPolicy:
        """Toggle a dynamic policy's enabled status."""
        return self._get_loop().run_until_complete(
            self._async_client.toggle_dynamic_policy(policy_id, enabled)
        )

    def get_effective_dynamic_policies(
        self,
        options: EffectivePoliciesOptions | None = None,
    ) -> list[DynamicPolicy]:
        """Get effective dynamic policies with tier inheritance applied."""
        return self._get_loop().run_until_complete(
            self._async_client.get_effective_dynamic_policies(options)
        )

    # Code Governance sync wrappers

    def validate_git_provider(
        self,
        request: ValidateGitProviderRequest,
    ) -> ValidateGitProviderResponse:
        """Validate Git provider credentials before configuration."""
        return self._get_loop().run_until_complete(
            self._async_client.validate_git_provider(request)
        )

    def configure_git_provider(
        self,
        request: ConfigureGitProviderRequest,
    ) -> ConfigureGitProviderResponse:
        """Configure a Git provider for code governance."""
        return self._get_loop().run_until_complete(
            self._async_client.configure_git_provider(request)
        )

    def list_git_providers(self) -> ListGitProvidersResponse:
        """List all configured Git providers for the tenant."""
        return self._get_loop().run_until_complete(self._async_client.list_git_providers())

    def delete_git_provider(self, provider_type: GitProviderType) -> None:
        """Delete a configured Git provider."""
        return self._get_loop().run_until_complete(
            self._async_client.delete_git_provider(provider_type)
        )

    def create_pr(self, request: CreatePRRequest) -> CreatePRResponse:
        """Create a Pull Request from LLM-generated code."""
        return self._get_loop().run_until_complete(self._async_client.create_pr(request))

    def list_prs(
        self,
        options: ListPRsOptions | None = None,
    ) -> ListPRsResponse:
        """List Pull Requests created through code governance."""
        return self._get_loop().run_until_complete(self._async_client.list_prs(options))

    def get_pr(self, pr_id: str) -> PRRecord:
        """Get a specific PR record by ID."""
        return self._get_loop().run_until_complete(self._async_client.get_pr(pr_id))

    def sync_pr_status(self, pr_id: str) -> PRRecord:
        """Sync PR status with the Git provider."""
        return self._get_loop().run_until_complete(self._async_client.sync_pr_status(pr_id))

    def get_code_governance_metrics(self) -> CodeGovernanceMetrics:
        """Get aggregated code governance metrics."""
        return self._get_loop().run_until_complete(self._async_client.get_code_governance_metrics())

    def export_code_governance_data(
        self,
        options: ExportOptions | None = None,
    ) -> ExportResponse:
        """Export code governance data for compliance reporting."""
        return self._get_loop().run_until_complete(
            self._async_client.export_code_governance_data(options)
        )
