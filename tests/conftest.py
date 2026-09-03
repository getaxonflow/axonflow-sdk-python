"""Pytest fixtures for AxonFlow SDK tests.

This module provides fixtures for both unit tests (mocked) and contract tests
(using recorded API responses from fixtures/).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from axonflow import AxonFlow


class _EgressBlocked(BaseException):
    """Raised by the egress guard below. Deliberately a BaseException.

    The telemetry path catches ``Exception`` broadly and MUST — an explicit
    tuple there was a fail-CLOSED trap, because ``httpx.InvalidURL`` does not
    subclass ``httpx.HTTPError`` and a malformed endpoint raised straight out
    of ``_send_telemetry_ping_now`` (see axonflow/telemetry.py).

    But a guard the code under test can swallow is not a guard. When this was
    a ``RuntimeError``, the broad catch turned it into
    ``PlatformHealthProbe(None, None)`` and ``False`` — so a test that deleted
    AXONFLOW_TELEMETRY and forgot a transport would pass VACUOUSLY against an
    empty probe and an undelivered ping, which is precisely the scenario this
    fixture's own docstring says it exists to catch.

    Deriving from BaseException puts it in the same class as KeyboardInterrupt
    and SystemExit: production code's ``except Exception`` cannot catch it, so
    the guard reaches the test runner as a loud failure however broadly the
    code under test catches.
    """


@pytest.fixture(autouse=True)
def _disable_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable telemetry in all tests AND block real HTTP egress from the
    telemetry path.

    The AXONFLOW_TELEMETRY=off env var is the canonical opt-out, but a
    future test could legitimately delete it (to exercise the telemetry
    path itself) and the suite would start firing real pings at the prod
    checkpoint. Defensive: also patch the httpx.get / httpx.post call
    sites the telemetry module uses so a deleted opt-out env can't leak.
    """
    import httpx

    monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")

    def _blocked_http(*_args, **_kwargs):
        raise _EgressBlocked(
            "Real HTTP egress is blocked in unit tests. "
            "Use httpx.MockTransport or a recorded fixture for tests that "
            "exercise the telemetry path."
        )

    monkeypatch.setattr(httpx, "get", _blocked_http)
    monkeypatch.setattr(httpx, "post", _blocked_http)


@pytest.fixture(autouse=True)
def _isolate_adapter_registry():
    """Reset the process-global adapter registry around every test.

    The registry is module-global BY DESIGN — an adapter constructed anywhere
    in the process is genuinely in use, and that is exactly what the telemetry
    heartbeat should report. In a test session that same property is
    cross-test pollution: ``tests/test_langgraph_adapter.py`` constructs
    ``AxonFlowLangGraphAdapter`` dozens of times, each of which registers
    ``langgraph``, and any later test asserting ``features == []`` then fails
    depending on collection order.

    Autouse and in conftest for the same reason ``_disable_telemetry`` is:
    the isolation has to hold for tests that have never heard of the registry,
    not only for the ones that use it deliberately.
    """
    from axonflow import telemetry as _tel

    previous = _tel._reset_adapter_registry_for_test()  # noqa: SLF001
    try:
        yield
    finally:
        _tel._restore_adapter_registry_for_test(previous)  # noqa: SLF001


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
        "endpoint": "https://test.axonflow.com",
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
        return load_json_fixture("connector_list_response")  # type: ignore[return-value]
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
