"""Tests for policy simulation methods (Evaluation Tier+)."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError
from axonflow.types import (
    ImpactReportResponse,
    PolicyConflictResponse,
    SimulatePoliciesResponse,
)


class TestSimulatePolicies:
    """Tests for simulate_policies method."""

    @pytest.mark.asyncio
    async def test_success_full_args(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test simulation with all optional arguments provided."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "allowed": False,
                    "applied_policies": ["pii-ssn", "pii-credit-card"],
                    "risk_score": 0.85,
                    "required_actions": ["redact_pii"],
                    "processing_time_ms": 12,
                    "total_policies": 5,
                    "dry_run": True,
                    "simulated_at": "2026-03-24T10:00:00Z",
                    "tier": "evaluation",
                    "daily_usage": {"used": 3, "limit": 100},
                },
            },
        )

        result = await client.simulate_policies(
            query="Show me SSN 123-45-6789",
            request_type="chat",
            user={"role": "analyst", "department": "finance"},
            client={"app": "dashboard"},
            context={"session_id": "sess-001"},
        )

        assert isinstance(result, SimulatePoliciesResponse)
        assert result.allowed is False
        assert result.applied_policies == ["pii-ssn", "pii-credit-card"]
        assert result.risk_score == 0.85
        assert result.required_actions == ["redact_pii"]
        assert result.processing_time_ms == 12
        assert result.total_policies == 5
        assert result.dry_run is True
        assert result.simulated_at == "2026-03-24T10:00:00Z"
        assert result.tier == "evaluation"
        assert result.daily_usage is not None
        assert result.daily_usage.used == 3
        assert result.daily_usage.limit == 100

        # Verify correct request body was sent
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body["query"] == "Show me SSN 123-45-6789"
        assert body["request_type"] == "chat"
        assert body["user"] == {"role": "analyst", "department": "finance"}
        assert body["client"] == {"app": "dashboard"}
        assert body["context"] == {"session_id": "sess-001"}

    @pytest.mark.asyncio
    async def test_success_minimal_args(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test simulation with only required query argument."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "allowed": True,
                    "applied_policies": [],
                    "risk_score": 0.0,
                    "required_actions": [],
                    "processing_time_ms": 5,
                    "total_policies": 3,
                    "dry_run": True,
                    "simulated_at": "2026-03-24T10:00:00Z",
                    "tier": "evaluation",
                },
            },
        )

        result = await client.simulate_policies(query="What is the weather?")

        assert isinstance(result, SimulatePoliciesResponse)
        assert result.allowed is True
        assert result.applied_policies == []
        assert result.risk_score == 0.0
        assert result.daily_usage is None

        # Verify only query is in the body
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body == {"query": "What is the weather?"}
        assert "request_type" not in body
        assert "user" not in body
        assert "client" not in body
        assert "context" not in body

    @pytest.mark.asyncio
    async def test_without_data_wrapper(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test simulation response without data wrapper (flat response)."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "allowed": True,
                "applied_policies": [],
                "risk_score": 0.1,
                "total_policies": 2,
                "dry_run": True,
                "simulated_at": "2026-03-24T10:00:00Z",
                "tier": "evaluation",
            },
        )

        result = await client.simulate_policies(query="Hello")

        assert result.allowed is True
        assert result.risk_score == 0.1

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=403,
            json={"error": "Policy simulation requires Evaluation tier or above"},
        )

        with pytest.raises(AxonFlowError):
            await client.simulate_policies(query="test")


class TestGetPolicyImpactReport:
    """Tests for get_policy_impact_report method."""

    @pytest.mark.asyncio
    async def test_success(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test successful impact report generation."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "policy_id": "pol_abc123",
                    "policy_name": "PII Detection",
                    "total_inputs": 3,
                    "matched": 2,
                    "blocked": 1,
                    "match_rate": 0.6667,
                    "block_rate": 0.3333,
                    "results": [
                        {
                            "input_index": 0,
                            "matched": True,
                            "blocked": True,
                            "actions": ["block", "log"],
                        },
                        {
                            "input_index": 1,
                            "matched": True,
                            "blocked": False,
                            "actions": ["redact"],
                        },
                        {
                            "input_index": 2,
                            "matched": False,
                            "blocked": False,
                            "actions": [],
                        },
                    ],
                    "processing_time_ms": 45,
                    "generated_at": "2026-03-24T10:00:00Z",
                    "tier": "evaluation",
                },
            },
        )

        result = await client.get_policy_impact_report(
            policy_id="pol_abc123",
            inputs=[
                {"query": "SSN is 123-45-6789"},
                {"query": "Email is user@example.com"},
                {"query": "The weather is nice"},
            ],
        )

        assert isinstance(result, ImpactReportResponse)
        assert result.policy_id == "pol_abc123"
        assert result.policy_name == "PII Detection"
        assert result.total_inputs == 3
        assert result.matched == 2
        assert result.blocked == 1
        assert result.match_rate == pytest.approx(0.6667)
        assert result.block_rate == pytest.approx(0.3333)
        assert len(result.results) == 3
        assert result.results[0].matched is True
        assert result.results[0].blocked is True
        assert result.results[0].actions == ["block", "log"]
        assert result.results[2].matched is False
        assert result.results[2].actions == []
        assert result.processing_time_ms == 45
        assert result.tier == "evaluation"

        # Verify request body
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body["policy_id"] == "pol_abc123"
        assert len(body["inputs"]) == 3

    @pytest.mark.asyncio
    async def test_without_data_wrapper(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test impact report response without data wrapper."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "policy_id": "pol_xyz",
                "total_inputs": 1,
                "matched": 0,
                "blocked": 0,
                "match_rate": 0.0,
                "block_rate": 0.0,
                "results": [],
                "generated_at": "2026-03-24T10:00:00Z",
                "tier": "evaluation",
            },
        )

        result = await client.get_policy_impact_report(
            policy_id="pol_xyz",
            inputs=[{"query": "hello"}],
        )

        assert result.policy_id == "pol_xyz"
        assert result.matched == 0

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_policy_impact_report(
                policy_id="pol_abc",
                inputs=[{"query": "test"}],
            )


class TestDetectPolicyConflicts:
    """Tests for detect_policy_conflicts method."""

    @pytest.mark.asyncio
    async def test_with_policy_id(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test conflict detection scoped to a specific policy."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "conflicts": [
                        {
                            "policy_a": {
                                "id": "pol_001",
                                "name": "Block PII",
                                "type": "dynamic",
                            },
                            "policy_b": {
                                "id": "pol_002",
                                "name": "Allow Analytics",
                                "type": "dynamic",
                            },
                            "conflict_type": "action_contradiction",
                            "description": "Block PII blocks email patterns that Allow Analytics permits",
                            "severity": "high",
                            "overlapping_field": "query",
                        },
                    ],
                    "total_policies": 10,
                    "conflict_count": 1,
                    "checked_at": "2026-03-24T10:00:00Z",
                    "tier": "evaluation",
                },
            },
        )

        result = await client.detect_policy_conflicts(policy_id="pol_001")

        assert isinstance(result, PolicyConflictResponse)
        assert result.conflict_count == 1
        assert result.total_policies == 10
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.policy_a.id == "pol_001"
        assert conflict.policy_a.name == "Block PII"
        assert conflict.policy_b.id == "pol_002"
        assert conflict.conflict_type == "action_contradiction"
        assert conflict.severity == "high"
        assert conflict.overlapping_field == "query"
        assert result.tier == "evaluation"

        # Verify request body includes policy_id
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body == {"policy_id": "pol_001"}

    @pytest.mark.asyncio
    async def test_without_policy_id(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test conflict detection across all policies (no policy_id)."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "data": {
                    "conflicts": [],
                    "total_policies": 5,
                    "conflict_count": 0,
                    "checked_at": "2026-03-24T10:00:00Z",
                    "tier": "evaluation",
                },
            },
        )

        result = await client.detect_policy_conflicts()

        assert isinstance(result, PolicyConflictResponse)
        assert result.conflict_count == 0
        assert result.conflicts == []
        assert result.total_policies == 5

        # Verify empty body (no policy_id key)
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body == {}

    @pytest.mark.asyncio
    async def test_without_data_wrapper(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test conflict response without data wrapper."""
        httpx_mock.add_response(
            status_code=200,
            json={
                "conflicts": [],
                "total_policies": 3,
                "conflict_count": 0,
                "checked_at": "2026-03-24T10:00:00Z",
                "tier": "evaluation",
            },
        )

        result = await client.detect_policy_conflicts()

        assert result.total_policies == 3
        assert result.conflict_count == 0

    @pytest.mark.asyncio
    async def test_server_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that server errors are raised as AxonFlowError."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.detect_policy_conflicts()
