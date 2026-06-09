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


class ObligationNotFulfillableError(AxonFlowError):
    """A Decision Mode obligation could not be discharged through the engine.

    Raised by the PEP fulfillment path (``client.fulfill_request`` /
    ``client.decide_and_fulfill``) when a ``redact_pii`` obligation named no
    request-phase fulfillment endpoint, named an endpoint the client will not
    call, advertised a content-type the PEP is not holding, the engine endpoint
    failed, or the engine reported the redactor did not run
    (``redaction_evaluated=false``).

    This is a FAIL-CLOSED signal (ADR-056 / #2563): the caller MUST block the
    request, never forward the unredacted content. The PEP contains no local
    redaction path, so it cannot silently substitute its own masking.
    """


class UpgradeInfo:
    """Pricing-tier upgrade context emitted in a V1 429 envelope.

    Mirrors the platform-side
    feedback_429_no_upgrade_hint_is_conversion_gap.md contract.
    Cross-SDK parity:

      Go:     axonflow-sdk-go/decisions.go (UpgradeInfo)
      TS:     axonflow-sdk-typescript/src/types/decisions.ts (UpgradeInfo)
      Java:   .../sdk/types/UpgradeInfo.java
      Rust:   axonflow-sdk-rust/src/types/decisions.rs (UpgradeInfo)
    """

    __slots__ = ("buy_url", "compare_url", "tier", "wording")

    def __init__(
        self,
        tier: str,
        wording: str,
        compare_url: str,
        buy_url: str,
    ) -> None:
        self.tier = tier
        self.wording = wording
        self.compare_url = compare_url
        self.buy_url = buy_url


class RateLimitError(AxonFlowError):
    """Rate limit exceeded.

    Holds the parsed V1 429 envelope when available. ``limit_type`` and
    ``tier`` and ``upgrade`` are populated for tier-cap 429s (e.g.
    ``list_decisions`` page-size cap, daily-quota cap); legacy 429s
    leave them ``None`` for backwards compatibility.
    """

    def __init__(
        self,
        message: str,
        limit: int,
        remaining: int,
        reset_at: str | None = None,
        *,
        limit_type: str | None = None,
        tier: str | None = None,
        upgrade: UpgradeInfo | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at,
                "limit_type": limit_type,
                "tier": tier,
            },
        )
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        self.limit_type = limit_type
        self.tier = tier
        self.upgrade = upgrade


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


class VersionConflictError(AxonFlowError):
    """Plan version conflict (HTTP 409).

    Raised when an update_plan request fails due to optimistic concurrency
    control — the plan was modified since the expected_version was read.
    """

    def __init__(
        self,
        plan_id: str,
        expected_version: int,
        current_version: int | None = None,
    ) -> None:
        msg = f"Version conflict for plan {plan_id}: expected version {expected_version}"
        if current_version is not None:
            msg += f", current version {current_version}"
        super().__init__(
            msg,
            details={
                "plan_id": plan_id,
                "expected_version": expected_version,
                "current_version": current_version,
            },
        )
        self.plan_id = plan_id
        self.expected_version = expected_version
        self.current_version = current_version


class IdempotencyKeyMismatchError(AxonFlowError):
    """Idempotency key mismatch on a step gate or complete call (HTTP 409).

    Raised when an ``idempotency_key`` on a ``/gate`` or ``/complete`` request conflicts
    with the key recorded on an earlier gate call for the same ``(workflow_id, step_id)``.
    Maps to HTTP 409 with ``error.code == "IDEMPOTENCY_KEY_MISMATCH"``.

    ``expected_idempotency_key`` is the empty string ``""`` when the gate call had no
    key but complete did; conversely ``received_idempotency_key`` is ``""`` when complete
    omitted a key that gate had set.
    """

    def __init__(
        self,
        message: str,
        workflow_id: str,
        step_id: str,
        expected_idempotency_key: str,
        received_idempotency_key: str,
    ) -> None:
        super().__init__(
            message,
            details={
                "workflow_id": workflow_id,
                "step_id": step_id,
                "expected_idempotency_key": expected_idempotency_key,
                "received_idempotency_key": received_idempotency_key,
            },
        )
        self.workflow_id = workflow_id
        self.step_id = step_id
        self.expected_idempotency_key = expected_idempotency_key
        self.received_idempotency_key = received_idempotency_key
