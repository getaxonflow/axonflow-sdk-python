"""Contract tests for AxonFlow SDK.

These tests validate that the SDK can correctly parse real API responses
from the AxonFlow Agent. They use recorded fixtures to ensure the SDK's
Pydantic models match the actual API contract.

This prevents issues like:
- Datetime parsing failures (nanoseconds unhandled)
- Missing fields in response models
- Type mismatches between API and SDK

Run: pytest tests/test_contract.py -v
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from axonflow.types import (
    AuditResult,
    ClientResponse,
    ConnectorMetadata,
    PlanStep,
    PolicyApprovalResult,
    PolicyEvaluationInfo,
    RateLimitInfo,
)

from .conftest import fixture_exists, load_json_fixture


def load_fixture(name: str) -> dict[str, Any] | list[Any]:
    """Load a JSON fixture file, skipping test if not found."""
    if not fixture_exists(name):
        pytest.skip(f"Fixture not found: {name}")
    return load_json_fixture(name)


class TestHealthResponseContract:
    """Test health response parsing."""

    def test_health_response_structure(self) -> None:
        """Verify health response has expected fields."""
        data = load_fixture("health_response")

        assert "status" in data
        assert data["status"] == "healthy"
        assert "version" in data
        assert "timestamp" in data


class TestClientResponseContract:
    """Test ClientResponse model against real API responses."""

    def test_successful_query_response_parses(self) -> None:
        """Verify successful query response can be parsed by SDK."""
        data = load_fixture("successful_query_response")

        # This is the critical test - SDK must parse real API response
        response = ClientResponse.model_validate(data)

        assert response.success is True
        assert response.blocked is False
        assert response.data is not None
        assert response.error is None
        assert response.block_reason is None

    def test_successful_query_has_policy_info(self) -> None:
        """Verify policy_info is correctly parsed."""
        data = load_fixture("successful_query_response")
        response = ClientResponse.model_validate(data)

        assert response.policy_info is not None
        assert isinstance(response.policy_info, PolicyEvaluationInfo)
        assert len(response.policy_info.policies_evaluated) > 0
        assert response.policy_info.processing_time

    def test_blocked_query_response_parses(self) -> None:
        """Verify blocked query response can be parsed by SDK."""
        data = load_fixture("blocked_query_pii_response")

        response = ClientResponse.model_validate(data)

        assert response.success is False
        assert response.blocked is True
        assert response.block_reason is not None
        assert "PII" in response.block_reason

    def test_blocked_query_has_policy_info(self) -> None:
        """Verify blocked response includes policy evaluation details."""
        data = load_fixture("blocked_query_pii_response")
        response = ClientResponse.model_validate(data)

        assert response.policy_info is not None
        assert len(response.policy_info.policies_evaluated) > 0
        # Should include PII-related policies
        policies_str = " ".join(response.policy_info.policies_evaluated).lower()
        assert "pii" in policies_str

    def test_plan_generation_response_parses(self) -> None:
        """Verify plan generation response can be parsed by SDK."""
        data = load_fixture("plan_generation_response")

        response = ClientResponse.model_validate(data)

        assert response.success is True
        assert response.plan_id is not None
        assert response.data is not None
        assert "steps" in response.data

    def test_plan_steps_parse_correctly(self) -> None:
        """Verify plan steps can be parsed into PlanStep models."""
        data = load_fixture("plan_generation_response")
        response = ClientResponse.model_validate(data)

        steps_data = response.data.get("steps", [])
        assert len(steps_data) > 0

        # Parse each step
        steps = [PlanStep.model_validate(s) for s in steps_data]

        assert len(steps) >= 2
        assert steps[0].id
        assert steps[0].name
        assert steps[0].type

        # Verify dependency structure
        dependent_step = next((s for s in steps if s.depends_on), None)
        assert dependent_step is not None, "Expected at least one step with dependencies"


class TestPolicyApprovalContract:
    """Test PolicyApprovalResult (Gateway Mode) against real API responses."""

    def test_policy_context_approved_parses(self) -> None:
        """Verify Gateway Mode pre-check approved response can be parsed."""
        from axonflow.client import _parse_datetime

        data = load_fixture("policy_context_response")

        # Manual parsing similar to client code
        rate_limit = None
        if data.get("rate_limit"):
            rate_limit = RateLimitInfo(
                limit=data["rate_limit"]["limit"],
                remaining=data["rate_limit"]["remaining"],
                reset_at=_parse_datetime(data["rate_limit"]["reset_at"]),
            )

        result = PolicyApprovalResult(
            context_id=data["context_id"],
            approved=data["approved"],
            approved_data=data.get("approved_data", {}),
            policies=data.get("policies", []),
            rate_limit_info=rate_limit,
            expires_at=_parse_datetime(data["expires_at"]),
            block_reason=data.get("block_reason"),
        )

        assert result.context_id
        assert result.approved is True
        assert len(result.policies) > 0
        assert result.expires_at is not None
        assert result.block_reason is None

    def test_policy_context_blocked_parses(self) -> None:
        """Verify Gateway Mode pre-check blocked response can be parsed."""
        from axonflow.client import _parse_datetime

        data = load_fixture("policy_context_blocked_response")

        rate_limit = None
        if data.get("rate_limit"):
            rate_limit = RateLimitInfo(
                limit=data["rate_limit"]["limit"],
                remaining=data["rate_limit"]["remaining"],
                reset_at=_parse_datetime(data["rate_limit"]["reset_at"]),
            )

        result = PolicyApprovalResult(
            context_id=data["context_id"],
            approved=data["approved"],
            approved_data=data.get("approved_data", {}),
            policies=data.get("policies", []),
            rate_limit_info=rate_limit,
            expires_at=_parse_datetime(data["expires_at"]),
            block_reason=data.get("block_reason"),
        )

        assert result.context_id
        assert result.approved is False
        assert result.block_reason is not None

    def test_datetime_with_nanoseconds_parses(self) -> None:
        """Verify datetime with nanosecond precision is handled.

        This was a real bug - Python's fromisoformat() only supports
        up to 6 fractional digits, but the API returns 9.
        """
        from axonflow.client import _parse_datetime

        data = load_fixture("policy_context_response")

        # The fixture contains nanosecond timestamps
        expires_at = data["expires_at"]
        assert "." in expires_at, "Fixture should have fractional seconds"

        # Should not raise
        parsed = _parse_datetime(expires_at)
        assert isinstance(parsed, datetime)

    def test_rate_limit_info_parses(self) -> None:
        """Verify rate limit information is correctly parsed."""
        from axonflow.client import _parse_datetime

        data = load_fixture("policy_context_response")

        assert "rate_limit" in data
        rate_limit_data = data["rate_limit"]

        rate_limit = RateLimitInfo(
            limit=rate_limit_data["limit"],
            remaining=rate_limit_data["remaining"],
            reset_at=_parse_datetime(rate_limit_data["reset_at"]),
        )

        assert rate_limit.limit == 100
        assert rate_limit.remaining == 97
        assert isinstance(rate_limit.reset_at, datetime)

    def test_approved_data_structure(self) -> None:
        """Verify approved_data contains expected healthcare data structure."""
        data = load_fixture("policy_context_response")

        approved_data = data.get("approved_data", {})
        assert "patients" in approved_data
        assert len(approved_data["patients"]) > 0

        # Verify PII redaction is in place
        for patient in approved_data["patients"]:
            assert patient.get("name") == "[REDACTED]"


class TestAuditContract:
    """Test AuditResult against real API responses."""

    def test_audit_response_parses(self) -> None:
        """Verify audit response can be parsed by SDK."""
        data = load_fixture("audit_response")

        result = AuditResult.model_validate(data)

        assert result.success is True
        assert result.audit_id
        assert len(result.audit_id) > 0


class TestConnectorContract:
    """Test ConnectorMetadata against real API responses."""

    def test_connector_list_parses(self) -> None:
        """Verify connector list response can be parsed."""
        data = load_fixture("connector_list_response")

        assert isinstance(data, list)
        assert len(data) > 0

        connectors = [ConnectorMetadata.model_validate(c) for c in data]

        assert len(connectors) >= 2

    def test_connector_has_required_fields(self) -> None:
        """Verify connectors have all required fields."""
        data = load_fixture("connector_list_response")
        connectors = [ConnectorMetadata.model_validate(c) for c in data]

        for connector in connectors:
            assert connector.id
            assert connector.name
            assert connector.type
            assert connector.version

    def test_connector_capabilities(self) -> None:
        """Verify connector capabilities are parsed as list."""
        data = load_fixture("connector_list_response")
        connectors = [ConnectorMetadata.model_validate(c) for c in data]

        # Find a connector with capabilities
        connector_with_caps = next((c for c in connectors if c.capabilities), None)
        assert connector_with_caps is not None
        assert isinstance(connector_with_caps.capabilities, list)

    def test_connector_health_status(self) -> None:
        """Verify connector health and installed status."""
        data = load_fixture("connector_list_response")
        connectors = [ConnectorMetadata.model_validate(c) for c in data]

        # Should have both healthy and unhealthy connectors
        healthy = [c for c in connectors if c.healthy]
        unhealthy = [c for c in connectors if not c.healthy]

        assert len(healthy) > 0, "Should have at least one healthy connector"
        assert len(unhealthy) > 0, "Should have at least one unhealthy connector"


class TestEdgeCases:
    """Test edge cases and potential failure modes."""

    def test_empty_policy_info_handled(self) -> None:
        """Verify response without policy_info is handled."""
        data = {
            "success": True,
            "data": {"result": "test"},
            "blocked": False,
        }

        response = ClientResponse.model_validate(data)
        assert response.success is True
        assert response.policy_info is None

    def test_minimal_response_parses(self) -> None:
        """Verify minimal response with only required fields parses."""
        data = {
            "success": True,
            "blocked": False,
        }

        response = ClientResponse.model_validate(data)
        assert response.success is True

    def test_unknown_fields_ignored(self) -> None:
        """Verify unknown fields from API don't break parsing."""
        data = {
            "success": True,
            "blocked": False,
            "some_new_field": "should be ignored",
            "another_unknown": {"nested": "data"},
        }

        # Should not raise
        response = ClientResponse.model_validate(data)
        assert response.success is True

    def test_null_vs_missing_fields(self) -> None:
        """Verify null values are handled correctly."""
        data = {
            "success": True,
            "data": None,
            "result": None,
            "plan_id": None,
            "error": None,
            "blocked": False,
            "block_reason": None,
            "policy_info": None,
        }

        response = ClientResponse.model_validate(data)
        assert response.data is None
        assert response.result is None
        assert response.policy_info is None
