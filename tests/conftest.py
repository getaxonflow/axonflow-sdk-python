"""Pytest fixtures for AxonFlow SDK tests.

This module provides fixtures for both unit tests (mocked) and contract tests
(using recorded API responses from fixtures/).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow

# ============================================================================
# Fixture Loading Utilities
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_json_fixture(name: str) -> dict[str, Any] | list[Any]:
    """Load a JSON fixture file by name.

    Args:
        name: Fixture name (without .json extension)

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: If fixture doesn't exist
    """
    filepath = FIXTURES_DIR / f"{name}.json"
    with filepath.open() as f:
        return json.load(f)


def fixture_exists(name: str) -> bool:
    """Check if a fixture file exists."""
    return (FIXTURES_DIR / f"{name}.json").exists()


# ============================================================================
# Base Configuration Fixtures
# ============================================================================


@pytest.fixture
def config_dict() -> dict[str, Any]:
    """Base configuration dictionary."""
    return {
        "agent_url": "https://test.axonflow.com",
        "client_id": "test-client",
        "client_secret": "test-secret",
        "debug": True,
    }


@pytest_asyncio.fixture
async def client(config_dict: dict[str, Any]) -> AsyncGenerator[AxonFlow, None]:
    """Create test AxonFlow client."""
    async with AxonFlow(**config_dict) as c:
        yield c


@pytest.fixture
def sync_client(config_dict: dict[str, Any]):
    """Create sync test AxonFlow client."""
    with AxonFlow.sync(**config_dict) as c:
        yield c


# ============================================================================
# JSON Fixture-Based Response Fixtures
# ============================================================================


@pytest.fixture
def fixture_health_response() -> dict[str, Any]:
    """Load health response from fixture file."""
    if fixture_exists("health_response"):
        return load_json_fixture("health_response")
    # Fallback for backwards compatibility
    return {
        "status": "healthy",
        "version": "1.0.0",
        "components": {
            "database": "connected",
            "orchestrator": "reachable",
        },
    }


@pytest.fixture
def fixture_successful_query() -> dict[str, Any]:
    """Load successful query response from fixture file."""
    if fixture_exists("successful_query_response"):
        return load_json_fixture("successful_query_response")
    # Fallback
    return {
        "success": True,
        "data": {"result": "test result"},
        "blocked": False,
        "metadata": {},
        "policy_info": {
            "policies_evaluated": ["default"],
            "static_checks": [],
            "processing_time": "5ms",
            "tenant_id": "test",
        },
    }


@pytest.fixture
def fixture_blocked_pii() -> dict[str, Any]:
    """Load blocked (PII) query response from fixture file."""
    if fixture_exists("blocked_query_pii_response"):
        return load_json_fixture("blocked_query_pii_response")
    # Fallback
    return {
        "success": False,
        "blocked": True,
        "block_reason": "PII detected: SSN pattern found",
        "error": "Request blocked by policy",
        "policy_info": {
            "policies_evaluated": ["pii-ssn"],
            "static_checks": ["pii-detection"],
            "processing_time": "2ms",
            "tenant_id": "test",
        },
    }


@pytest.fixture
def fixture_plan_response() -> dict[str, Any]:
    """Load plan generation response from fixture file."""
    if fixture_exists("plan_generation_response"):
        return load_json_fixture("plan_generation_response")
    # Fallback
    return {
        "success": True,
        "plan_id": "plan-123",
        "data": {
            "steps": [
                {
                    "id": "step-1",
                    "name": "Fetch data",
                    "type": "data",
                    "description": "Fetch customer data",
                    "depends_on": [],
                    "agent": "data-agent",
                    "parameters": {},
                },
            ],
            "domain": "generic",
            "complexity": 1,
            "parallel": False,
        },
        "metadata": {},
    }


@pytest.fixture
def fixture_policy_context() -> dict[str, Any]:
    """Load Gateway Mode policy context response from fixture file."""
    if fixture_exists("policy_context_response"):
        return load_json_fixture("policy_context_response")
    # Fallback
    return {
        "context_id": "ctx-123",
        "approved": True,
        "approved_data": {"patients": ["patient-1"]},
        "policies": ["hipaa", "gdpr"],
        "rate_limit": {
            "limit": 100,
            "remaining": 99,
            "reset_at": "2025-12-15T00:00:00Z",
        },
        "expires_at": "2025-12-15T00:00:00Z",
        "block_reason": None,
    }


@pytest.fixture
def fixture_connector_list() -> list[dict[str, Any]]:
    """Load connector list response from fixture file."""
    if fixture_exists("connector_list_response"):
        return load_json_fixture("connector_list_response")
    # Fallback
    return [
        {
            "id": "postgres",
            "name": "PostgreSQL",
            "type": "database",
            "version": "1.0.0",
            "description": "PostgreSQL database connector",
            "category": "database",
            "tags": ["sql", "relational"],
            "capabilities": ["read", "write"],
            "config_schema": {},
            "installed": True,
            "healthy": True,
        },
    ]


# ============================================================================
# Legacy Mock Fixtures (for backwards compatibility with existing tests)
# ============================================================================


@pytest.fixture
def mock_health_response() -> dict[str, Any]:
    """Mock health check response."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "components": {
            "database": "connected",
            "orchestrator": "reachable",
        },
    }


@pytest.fixture
def mock_query_response() -> dict[str, Any]:
    """Mock successful query response."""
    return {
        "success": True,
        "data": {"result": "test result"},
        "blocked": False,
        "metadata": {},
        "policy_info": {
            "policies_evaluated": ["default"],
            "static_checks": [],
            "processing_time": "5ms",
            "tenant_id": "test",
        },
    }


@pytest.fixture
def mock_blocked_response() -> dict[str, Any]:
    """Mock blocked query response."""
    return {
        "success": False,
        "blocked": True,
        "block_reason": "Rate limit exceeded",
        "error": "Request blocked by policy",
    }


@pytest.fixture
def mock_connector_list() -> list[dict[str, Any]]:
    """Mock connector list response."""
    return [
        {
            "id": "postgres",
            "name": "PostgreSQL",
            "type": "database",
            "version": "1.0.0",
            "description": "PostgreSQL database connector",
            "category": "database",
            "tags": ["sql", "relational"],
            "capabilities": ["read", "write"],
            "config_schema": {},
            "installed": True,
            "healthy": True,
        },
        {
            "id": "salesforce",
            "name": "Salesforce",
            "type": "crm",
            "version": "1.0.0",
            "description": "Salesforce CRM connector",
            "category": "crm",
            "tags": ["crm", "sales"],
            "capabilities": ["read"],
            "config_schema": {},
            "installed": False,
            "healthy": False,
        },
    ]


@pytest.fixture
def mock_plan_response() -> dict[str, Any]:
    """Mock plan generation response."""
    return {
        "success": True,
        "plan_id": "plan-123",
        "data": {
            "steps": [
                {
                    "id": "step-1",
                    "name": "Fetch data",
                    "type": "data",
                    "description": "Fetch customer data",
                    "depends_on": [],
                    "agent": "data-agent",
                    "parameters": {},
                },
                {
                    "id": "step-2",
                    "name": "Process data",
                    "type": "process",
                    "description": "Process the data",
                    "depends_on": ["step-1"],
                    "agent": "process-agent",
                    "parameters": {},
                },
            ],
            "domain": "generic",
            "complexity": 2,
            "parallel": False,
        },
        "metadata": {},
    }


@pytest.fixture
def mock_pre_check_response() -> dict[str, Any]:
    """Mock Gateway Mode pre-check response."""
    return {
        "context_id": "ctx-123",
        "approved": True,
        "approved_data": {"patients": ["patient-1", "patient-2"]},
        "policies": ["hipaa", "gdpr"],
        "rate_limit": {
            "limit": 100,
            "remaining": 99,
            "reset_at": "2025-12-05T00:00:00Z",
        },
        "expires_at": "2025-12-04T13:00:00Z",
        "block_reason": None,
    }


@pytest.fixture
def mock_audit_response() -> dict[str, Any]:
    """Mock Gateway Mode audit response."""
    return {
        "success": True,
        "audit_id": "audit-456",
    }
