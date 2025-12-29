"""Tests for Policy CRUD methods.

Part of Unified Policy Architecture v2.0.0.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.policies import (
    CreateDynamicPolicyRequest,
    CreatePolicyOverrideRequest,
    CreateStaticPolicyRequest,
    DynamicPolicy,
    DynamicPolicyConfig,
    ListDynamicPoliciesOptions,
    ListStaticPoliciesOptions,
    OverrideAction,
    PolicyAction,
    PolicyCategory,
    PolicyOverride,
    PolicySeverity,
    PolicyTier,
    PolicyVersion,
    StaticPolicy,
    TestPatternResult,
    UpdateDynamicPolicyRequest,
    UpdateStaticPolicyRequest,
)

# Sample test data
SAMPLE_STATIC_POLICY = {
    "id": "pol_123",
    "name": "Block SQL Injection",
    "description": "Blocks SQL injection attempts",
    "category": "security-sqli",
    "tier": "system",
    "pattern": "(?i)(union\\s+select|drop\\s+table)",
    "severity": "critical",
    "enabled": True,
    "action": "block",
    "createdAt": "2025-01-01T00:00:00Z",
    "updatedAt": "2025-01-01T00:00:00Z",
    "version": 1,
}

SAMPLE_DYNAMIC_POLICY = {
    "id": "dpol_456",
    "name": "Rate Limit API",
    "description": "Rate limit API calls",
    "category": "dynamic-cost",
    "tier": "organization",
    "enabled": True,
    "config": {
        "type": "rate-limit",
        "rules": {"maxRequestsPerMinute": 100},
        "action": "block",
    },
    "createdAt": "2025-01-01T00:00:00Z",
    "updatedAt": "2025-01-01T00:00:00Z",
    "version": 1,
}

SAMPLE_OVERRIDE = {
    "policy_id": "pol_123",
    "action_override": "warn",
    "override_reason": "Testing override",
    "created_at": "2025-01-01T00:00:00Z",
    "active": True,
}


@pytest.fixture
def client() -> AxonFlow:
    """Create a test client."""
    return AxonFlow(
        agent_url="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )


class TestStaticPolicies:
    """Tests for static policy methods."""

    @pytest.mark.asyncio
    async def test_list_static_policies(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test listing static policies."""
        httpx_mock.add_response(json=[SAMPLE_STATIC_POLICY])

        policies = await client.list_static_policies()

        assert len(policies) == 1
        assert policies[0].id == "pol_123"
        assert policies[0].name == "Block SQL Injection"

    @pytest.mark.asyncio
    async def test_list_static_policies_with_filters(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing static policies with filters."""
        httpx_mock.add_response(json=[SAMPLE_STATIC_POLICY])

        options = ListStaticPoliciesOptions(
            category=PolicyCategory.SECURITY_SQLI,
            tier=PolicyTier.SYSTEM,
            enabled=True,
            limit=10,
            offset=0,
        )
        policies = await client.list_static_policies(options)

        assert len(policies) == 1
        # Verify request contains filter params
        request = httpx_mock.get_request()
        assert "category=security-sqli" in str(request.url)
        assert "tier=system" in str(request.url)

    @pytest.mark.asyncio
    async def test_list_static_policies_with_organization_id(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing static policies filtered by organization ID."""
        httpx_mock.add_response(json=[SAMPLE_STATIC_POLICY])

        options = ListStaticPoliciesOptions(
            tier=PolicyTier.ORGANIZATION,
            organization_id="org_12345",
        )
        policies = await client.list_static_policies(options)

        assert len(policies) == 1
        request = httpx_mock.get_request()
        assert "organization_id=org_12345" in str(request.url)
        assert "tier=organization" in str(request.url)

    @pytest.mark.asyncio
    async def test_get_static_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test getting a specific static policy."""
        httpx_mock.add_response(json=SAMPLE_STATIC_POLICY)

        policy = await client.get_static_policy("pol_123")

        assert policy.id == "pol_123"
        assert policy.category == PolicyCategory.SECURITY_SQLI
        request = httpx_mock.get_request()
        assert "/api/v1/static-policies/pol_123" in str(request.url)

    @pytest.mark.asyncio
    async def test_create_static_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test creating a new static policy."""
        httpx_mock.add_response(json=SAMPLE_STATIC_POLICY)

        request = CreateStaticPolicyRequest(
            name="Block SQL Injection",
            category=PolicyCategory.SECURITY_SQLI,
            pattern="(?i)(union\\s+select|drop\\s+table)",
            severity=PolicySeverity.CRITICAL,
        )
        policy = await client.create_static_policy(request)

        assert policy.id == "pol_123"
        http_request = httpx_mock.get_request()
        assert http_request.method == "POST"
        assert "/api/v1/static-policies" in str(http_request.url)

    @pytest.mark.asyncio
    async def test_update_static_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test updating an existing static policy."""
        updated = {**SAMPLE_STATIC_POLICY, "severity": "high"}
        httpx_mock.add_response(json=updated)

        request = UpdateStaticPolicyRequest(severity=PolicySeverity.HIGH)
        policy = await client.update_static_policy("pol_123", request)

        assert policy.severity == PolicySeverity.HIGH
        http_request = httpx_mock.get_request()
        assert http_request.method == "PUT"

    @pytest.mark.asyncio
    async def test_delete_static_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test deleting a static policy."""
        httpx_mock.add_response(status_code=204)

        await client.delete_static_policy("pol_123")

        http_request = httpx_mock.get_request()
        assert http_request.method == "DELETE"
        assert "/api/v1/static-policies/pol_123" in str(http_request.url)

    @pytest.mark.asyncio
    async def test_toggle_static_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test toggling a static policy's enabled status."""
        toggled = {**SAMPLE_STATIC_POLICY, "enabled": False}
        httpx_mock.add_response(json=toggled)

        policy = await client.toggle_static_policy("pol_123", False)

        assert policy.enabled is False
        http_request = httpx_mock.get_request()
        assert http_request.method == "PATCH"

    @pytest.mark.asyncio
    async def test_get_effective_static_policies(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting effective static policies."""
        httpx_mock.add_response(json=[SAMPLE_STATIC_POLICY])

        policies = await client.get_effective_static_policies()

        assert len(policies) == 1
        request = httpx_mock.get_request()
        assert "/api/v1/static-policies/effective" in str(request.url)

    @pytest.mark.asyncio
    async def test_test_pattern(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test pattern testing."""
        httpx_mock.add_response(
            json={
                "valid": True,
                "matches": [
                    {"input": "SELECT * FROM users", "matched": True},
                    {"input": "Hello world", "matched": False},
                ],
            }
        )

        result = await client.test_pattern("(?i)select", ["SELECT * FROM users", "Hello world"])

        assert result.valid is True
        assert len(result.matches) == 2
        assert result.matches[0].matched is True
        assert result.matches[1].matched is False

    @pytest.mark.asyncio
    async def test_get_static_policy_versions(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting policy version history."""
        httpx_mock.add_response(
            json={
                "policy_id": "pol_123",
                "versions": [
                    {
                        "version": 2,
                        "changedAt": "2025-01-02T00:00:00Z",
                        "changeType": "updated",
                    },
                    {
                        "version": 1,
                        "changedAt": "2025-01-01T00:00:00Z",
                        "changeType": "created",
                    },
                ],
                "count": 2,
            }
        )

        versions = await client.get_static_policy_versions("pol_123")

        assert len(versions) == 2
        assert versions[0].version == 2
        request = httpx_mock.get_request()
        assert "/api/v1/static-policies/pol_123/versions" in str(request.url)


class TestPolicyOverrides:
    """Tests for policy override methods."""

    @pytest.mark.asyncio
    async def test_list_policy_overrides(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test listing all policy overrides."""
        httpx_mock.add_response(json={"overrides": [SAMPLE_OVERRIDE]})

        overrides = await client.list_policy_overrides()

        assert len(overrides) == 1
        assert overrides[0].policy_id == "pol_123"
        assert overrides[0].action_override == OverrideAction.WARN
        http_request = httpx_mock.get_request()
        assert http_request.method == "GET"
        assert "/api/v1/static-policies/overrides" in str(http_request.url)

    @pytest.mark.asyncio
    async def test_list_policy_overrides_empty(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing policy overrides when none exist."""
        httpx_mock.add_response(json={"overrides": []})

        overrides = await client.list_policy_overrides()

        assert len(overrides) == 0

    @pytest.mark.asyncio
    async def test_create_policy_override(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test creating a policy override."""
        httpx_mock.add_response(json=SAMPLE_OVERRIDE)

        request = CreatePolicyOverrideRequest(
            action_override=OverrideAction.WARN,
            override_reason="Testing override",
        )
        override = await client.create_policy_override("pol_123", request)

        assert override.action_override == OverrideAction.WARN
        assert override.override_reason == "Testing override"
        http_request = httpx_mock.get_request()
        assert http_request.method == "POST"
        assert "/api/v1/static-policies/pol_123/override" in str(http_request.url)

    @pytest.mark.asyncio
    async def test_delete_policy_override(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test deleting a policy override."""
        httpx_mock.add_response(status_code=204)

        await client.delete_policy_override("pol_123")

        http_request = httpx_mock.get_request()
        assert http_request.method == "DELETE"
        assert "/api/v1/static-policies/pol_123/override" in str(http_request.url)


class TestDynamicPolicies:
    """Tests for dynamic policy methods."""

    @pytest.mark.asyncio
    async def test_list_dynamic_policies(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test listing dynamic policies."""
        httpx_mock.add_response(json=[SAMPLE_DYNAMIC_POLICY])

        policies = await client.list_dynamic_policies()

        assert len(policies) == 1
        assert policies[0].id == "dpol_456"
        assert policies[0].name == "Rate Limit API"

    @pytest.mark.asyncio
    async def test_list_dynamic_policies_with_filters(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing dynamic policies with filters."""
        httpx_mock.add_response(json=[SAMPLE_DYNAMIC_POLICY])

        options = ListDynamicPoliciesOptions(
            category=PolicyCategory.DYNAMIC_COST,
            enabled=True,
        )
        await client.list_dynamic_policies(options)

        request = httpx_mock.get_request()
        assert "category=dynamic-cost" in str(request.url)
        assert "enabled=true" in str(request.url)

    @pytest.mark.asyncio
    async def test_get_dynamic_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test getting a specific dynamic policy."""
        httpx_mock.add_response(json=SAMPLE_DYNAMIC_POLICY)

        policy = await client.get_dynamic_policy("dpol_456")

        assert policy.id == "dpol_456"
        assert policy.config.type == "rate-limit"

    @pytest.mark.asyncio
    async def test_create_dynamic_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test creating a new dynamic policy."""
        httpx_mock.add_response(json=SAMPLE_DYNAMIC_POLICY)

        request = CreateDynamicPolicyRequest(
            name="Rate Limit API",
            category=PolicyCategory.DYNAMIC_COST,
            config=DynamicPolicyConfig(
                type="rate-limit",
                rules={"maxRequestsPerMinute": 100},
                action=PolicyAction.BLOCK,
            ),
        )
        policy = await client.create_dynamic_policy(request)

        assert policy.id == "dpol_456"
        http_request = httpx_mock.get_request()
        assert http_request.method == "POST"

    @pytest.mark.asyncio
    async def test_update_dynamic_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test updating a dynamic policy."""
        httpx_mock.add_response(json=SAMPLE_DYNAMIC_POLICY)

        request = UpdateDynamicPolicyRequest(
            config=DynamicPolicyConfig(
                type="rate-limit",
                rules={"maxRequestsPerMinute": 200},
                action=PolicyAction.BLOCK,
            )
        )
        policy = await client.update_dynamic_policy("dpol_456", request)

        assert policy is not None
        http_request = httpx_mock.get_request()
        assert http_request.method == "PUT"

    @pytest.mark.asyncio
    async def test_delete_dynamic_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test deleting a dynamic policy."""
        httpx_mock.add_response(status_code=204)

        await client.delete_dynamic_policy("dpol_456")

        http_request = httpx_mock.get_request()
        assert http_request.method == "DELETE"
        assert "/api/v1/policies/dpol_456" in str(http_request.url)

    @pytest.mark.asyncio
    async def test_toggle_dynamic_policy(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        """Test toggling a dynamic policy's enabled status."""
        toggled = {**SAMPLE_DYNAMIC_POLICY, "enabled": False}
        httpx_mock.add_response(json=toggled)

        policy = await client.toggle_dynamic_policy("dpol_456", False)

        assert policy.enabled is False

    @pytest.mark.asyncio
    async def test_get_effective_dynamic_policies(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting effective dynamic policies."""
        httpx_mock.add_response(json=[SAMPLE_DYNAMIC_POLICY])

        policies = await client.get_effective_dynamic_policies()

        assert len(policies) == 1
        request = httpx_mock.get_request()
        assert "/api/v1/policies/effective" in str(request.url)


class TestPolicyTypes:
    """Tests for policy type validation."""

    def test_static_policy_validation(self) -> None:
        """Test static policy model validation."""
        policy = StaticPolicy.model_validate(SAMPLE_STATIC_POLICY)
        assert policy.id == "pol_123"
        assert policy.category == PolicyCategory.SECURITY_SQLI
        assert policy.tier == PolicyTier.SYSTEM
        assert policy.action == PolicyAction.BLOCK

    def test_dynamic_policy_validation(self) -> None:
        """Test dynamic policy model validation."""
        policy = DynamicPolicy.model_validate(SAMPLE_DYNAMIC_POLICY)
        assert policy.id == "dpol_456"
        assert policy.category == PolicyCategory.DYNAMIC_COST
        assert policy.config.type == "rate-limit"

    def test_policy_override_validation(self) -> None:
        """Test policy override model validation."""
        override = PolicyOverride.model_validate(SAMPLE_OVERRIDE)
        assert override.policy_id == "pol_123"
        assert override.action_override == OverrideAction.WARN
        assert override.active is True

    def test_create_static_policy_request(self) -> None:
        """Test create static policy request model."""
        request = CreateStaticPolicyRequest(
            name="Test Policy",
            category=PolicyCategory.PII_GLOBAL,
            pattern=r"\b\d{3}-\d{2}-\d{4}\b",
            severity=PolicySeverity.MEDIUM,
        )
        assert request.name == "Test Policy"
        assert request.category == PolicyCategory.PII_GLOBAL
        assert request.enabled is True  # default

    def test_create_static_policy_request_with_organization(self) -> None:
        """Test create static policy request with organization ID (Enterprise)."""
        request = CreateStaticPolicyRequest(
            name="Org Policy",
            category=PolicyCategory.CUSTOM,
            tier=PolicyTier.ORGANIZATION,
            organization_id="org_12345",
            pattern=r"\bconfidential\b",
            severity=PolicySeverity.HIGH,
        )
        assert request.name == "Org Policy"
        assert request.tier == PolicyTier.ORGANIZATION
        assert request.organization_id == "org_12345"

    def test_create_dynamic_policy_request(self) -> None:
        """Test create dynamic policy request model."""
        request = CreateDynamicPolicyRequest(
            name="Test Dynamic",
            category=PolicyCategory.DYNAMIC_RISK,
            config=DynamicPolicyConfig(
                type="custom",
                rules={"threshold": 0.8},
                action=PolicyAction.WARN,
            ),
        )
        assert request.name == "Test Dynamic"
        assert request.config.action == PolicyAction.WARN
