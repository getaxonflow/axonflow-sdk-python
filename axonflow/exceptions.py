"""AxonFlow SDK Exceptions.

Custom exception hierarchy for clear error handling.
"""

from __future__ import annotations

from typing import Any


class AxonFlowError(Exception):
    """Base exception for all AxonFlow errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ConfigurationError(AxonFlowError):
    """Invalid configuration."""


class AuthenticationError(AxonFlowError):
    """Authentication failed."""


class PolicyViolationError(AxonFlowError):
    """Request blocked by policy."""

    def __init__(
        self,
        message: str,
        policy: str | None = None,
        block_reason: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={"policy": policy, "block_reason": block_reason},
        )
        self.policy = policy
        self.block_reason = block_reason


class RateLimitError(AxonFlowError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        limit: int,
        remaining: int,
        reset_at: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={"limit": limit, "remaining": remaining, "reset_at": reset_at},
        )
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at


class BudgetExceededError(AxonFlowError):
    """Budget limit exceeded (HTTP 402).

    Raised when a request is blocked due to budget constraints.
    """

    def __init__(
        self,
        message: str,
        budget_id: str | None = None,
        budget_name: str | None = None,
        used_usd: float = 0.0,
        limit_usd: float = 0.0,
        action: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "budget_id": budget_id,
                "budget_name": budget_name,
                "used_usd": used_usd,
                "limit_usd": limit_usd,
                "action": action,
            },
        )
        self.budget_id = budget_id
        self.budget_name = budget_name
        self.used_usd = used_usd
        self.limit_usd = limit_usd
        self.action = action


class ConnectionError(AxonFlowError):
    """Connection to AxonFlow Agent failed."""


class TimeoutError(AxonFlowError):
    """Request timed out."""


class ConnectorError(AxonFlowError):
    """MCP connector error."""

    def __init__(
        self,
        message: str,
        connector: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={"connector": connector, "operation": operation},
        )
        self.connector = connector
        self.operation = operation


class PlanExecutionError(AxonFlowError):
    """Multi-agent plan execution failed."""

    def __init__(
        self,
        message: str,
        plan_id: str | None = None,
        step: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={"plan_id": plan_id, "step": step},
        )
        self.plan_id = plan_id
        self.step = step
